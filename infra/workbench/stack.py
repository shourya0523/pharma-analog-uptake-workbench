from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

OPENROUTER_MODEL_EXTRACT = "openai/gpt-4o-mini"
OPENROUTER_MODEL_JUDGE = "openai/gpt-4o-mini"
OPENROUTER_SECRET_NAME = "pharma-workbench/openrouter-api-key"


def _context_bool(scope: Construct, key: str, default: bool = True) -> bool:
    val = scope.node.try_get_context(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class WorkbenchStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        enable_cloudfront = _context_bool(self, "enable_cloudfront", default=True)

        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        data_bucket = s3.Bucket(
            self,
            "Data",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        jobs_dlq = sqs.Queue(
            self,
            "JobsDlq",
            retention_period=Duration.days(14),
        )
        jobs_queue = sqs.Queue(
            self,
            "Jobs",
            visibility_timeout=Duration.seconds(900),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=jobs_dlq),
        )

        db_sg = ec2.SecurityGroup(self, "DbSecurityGroup", vpc=vpc, allow_all_outbound=True)
        api_sg = ec2.SecurityGroup(self, "ApiServiceSecurityGroup", vpc=vpc, allow_all_outbound=True)
        worker_sg = ec2.SecurityGroup(self, "WorkerSecurityGroup", vpc=vpc, allow_all_outbound=True)
        lb_sg = ec2.SecurityGroup(self, "ApiLBSecurityGroup", vpc=vpc, allow_all_outbound=True)

        db = rds.DatabaseInstance(
            self,
            "Db",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[db_sg],
            credentials=rds.Credentials.from_generated_secret("workbench"),
            database_name="workbench",
            allocated_storage=20,
            max_allocated_storage=100,
            publicly_accessible=False,
            storage_encrypted=True,
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
            backup_retention=Duration.days(1),
        )
        db.connections.allow_from(api_sg, ec2.Port.tcp(5432), "API to Postgres")
        db.connections.allow_from(worker_sg, ec2.Port.tcp(5432), "Worker to Postgres")

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        openrouter_secret = secretsmanager.Secret(
            self,
            "OpenRouterApiKey",
            secret_name=OPENROUTER_SECRET_NAME,
            description="OpenRouter API key for LLM extract, judge, and web search",
        )

        image = ecs.ContainerImage.from_asset(
            str(BACKEND_DIR),
            platform=ecr_assets.Platform.LINUX_ARM64,
        )
        fargate_platform = ecs.RuntimePlatform(
            cpu_architecture=ecs.CpuArchitecture.ARM64,
            operating_system_family=ecs.OperatingSystemFamily.LINUX,
        )

        common_env = {
            "ENVIRONMENT": "aws",
            "STORAGE_BACKEND": "s3",
            "JOB_BACKEND": "sqs",
            "S3_BUCKET": data_bucket.bucket_name,
            "SQS_QUEUE_URL": jobs_queue.queue_url,
            "AWS_REGION": Stack.of(self).region,
            "DB_HOST": db.db_instance_endpoint_address,
            "DB_NAME": "workbench",
            "SEC_USER_AGENT": "PharmaAnalogUptakeWorkbench research@example.com",
            "OPENROUTER_MODEL_EXTRACT": OPENROUTER_MODEL_EXTRACT,
            "OPENROUTER_MODEL_JUDGE": OPENROUTER_MODEL_JUDGE,
            "ENABLE_LLM_SEARCH": "true",
            "LLM_SEARCH_ENGINE": "auto",
            # SPA is same-origin via CloudFront /api proxy when CDN is enabled.
            "CORS_ORIGINS": "*",
        }

        common_secrets = {
            "DB_USER": ecs.Secret.from_secrets_manager(db.secret, field="username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db.secret, field="password"),
            "OPENROUTER_API_KEY": ecs.Secret.from_secrets_manager(openrouter_secret),
        }

        def grant_data_access(role: iam.IRole, *, worker: bool) -> None:
            data_bucket.grant_read_write(role)
            if worker:
                jobs_queue.grant_consume_messages(role)
            else:
                jobs_queue.grant_send_messages(role)
            openrouter_secret.grant_read(role)

        api_task = ecs.FargateTaskDefinition(
            self,
            "ApiTask",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=fargate_platform,
        )
        grant_data_access(api_task.task_role, worker=False)
        api_container = api_task.add_container(
            "api",
            image=image,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            environment=common_env,
            secrets=common_secrets,
        )
        api_container.add_port_mappings(ecs.PortMapping(container_port=8000))

        worker_task = ecs.FargateTaskDefinition(
            self,
            "WorkerTask",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=fargate_platform,
        )
        grant_data_access(worker_task.task_role, worker=True)
        worker_task.add_container(
            "worker",
            image=image,
            command=["python", "-m", "app.worker"],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            environment=common_env,
            secrets=common_secrets,
        )

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ApiLB",
            vpc=vpc,
            internet_facing=True,
            security_group=lb_sg,
        )
        listener = alb.add_listener("Http", port=80, open=True)

        api_service = ecs.FargateService(
            self,
            "ApiService",
            cluster=cluster,
            task_definition=api_task,
            desired_count=1,
            min_healthy_percent=50,
            assign_public_ip=True,
            security_groups=[api_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        api_sg.connections.allow_from(lb_sg, ec2.Port.tcp(8000), "Load balancer to target")

        listener.add_targets(
            "ApiTargets",
            port=8000,
            targets=[api_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        ecs.FargateService(
            self,
            "WorkerService",
            cluster=cluster,
            task_definition=worker_task,
            desired_count=1,
            min_healthy_percent=50,
            assign_public_ip=True,
            security_groups=[worker_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )

        CfnOutput(self, "ApiLoadBalancerDns", value=alb.load_balancer_dns_name)
        CfnOutput(self, "ApiUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "JobsQueueUrl", value=jobs_queue.queue_url)
        CfnOutput(self, "OpenRouterSecretArn", value=openrouter_secret.secret_arn)
        CfnOutput(self, "OpenRouterSecretName", value=openrouter_secret.secret_name)

        if not enable_cloudfront:
            CfnOutput(self, "CloudFrontEnabled", value="false")
            return

        web_bucket = s3.Bucket(
            self,
            "Web",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        api_rewrite = cloudfront.Function(
            self,
            "ApiRewrite",
            code=cloudfront.FunctionCode.from_inline(
                """
function handler(event) {
  var request = event.request;
  if (request.uri.startsWith('/api/')) {
    request.uri = request.uri.substring(4);
  } else if (request.uri === '/api') {
    request.uri = '/';
  }
  return request;
}
"""
            ),
        )

        distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/api*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        alb.load_balancer_dns_name,
                        protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
                        http_port=80,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=api_rewrite,
                            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        )
                    ],
                )
            },
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(1),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(1),
                ),
            ],
        )

        frontend_dist = FRONTEND_DIR / "dist"
        if not frontend_dist.is_dir():
            raise RuntimeError(
                f"Missing {frontend_dist}. Run: "
                "cd frontend && VITE_API_URL=/api npm ci && VITE_API_URL=/api npm run build"
            )

        s3deploy.BucketDeployment(
            self,
            "DeployWebsite",
            sources=[s3deploy.Source.asset(str(frontend_dist))],
            destination_bucket=web_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "CloudFrontUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "CloudFrontEnabled", value="true")
