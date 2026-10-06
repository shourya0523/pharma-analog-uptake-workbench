// Fetches a gold row's source document so the app can show it inline.
//
// GET /functions/v1/source?url=<source_url>  (Authorization: the reviewer's session)
//
// Only URLs that some gold row cites are fetched, and only for team members:
// the lookup runs as the caller, so the rows table's RLS decides both. That
// keeps this from being an open proxy. The response is the document's bytes
// with its content type, plus X-Final-Url (after redirects) so relative links
// in an HTML page can be resolved.
import { createClient } from "npm:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Expose-Headers": "x-final-url, content-type",
};

function reply(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  const target = new URL(req.url).searchParams.get("url");
  if (!target) return reply(400, "missing url");

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const asCaller = createClient(supabaseUrl, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } },
  });
  const { data: cited, error } = await asCaller
    .from("rows").select("gold_id").eq("source_url", target).limit(1);
  // 42501: the caller may not even ask (an anonymous key), which is a refusal too.
  if (error) return reply(error.code === "42501" ? 403 : 500, error.message);
  if (!cited?.length) return reply(403, "not a gold source, or not a team member");

  // SEC asks automated clients to name a contact; the project sets one in
  // app_config (key sec_contact). Other hosts get the same honest agent.
  const admin = createClient(supabaseUrl, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data: config } = await admin
    .from("app_config").select("value").eq("key", "sec_contact").maybeSingle();
  const agent = `GoldVerification/1.0 (document preview for a review team${
    config?.value ? `; ${config.value}` : ""})`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      headers: { "User-Agent": agent, Accept: "text/html,application/pdf,*/*" },
      redirect: "follow",
    });
  } catch (err) {
    return reply(502, `could not reach the source: ${err}`);
  }
  if (!upstream.ok) return reply(502, `source answered ${upstream.status}`);
  return new Response(upstream.body, {
    headers: {
      ...CORS,
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/octet-stream",
      "X-Final-Url": upstream.url,
      "Cache-Control": "private, max-age=86400",
    },
  });
});
