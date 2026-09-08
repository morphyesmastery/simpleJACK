# LEGEND.md — SimpleJack Skill Registry (public edition)

## HOW TO SEND COMMANDS (THE ONLY METHOD)

Any model can execute any skill below:

1. Find the verb in the list below
2. Copy the canonical command line
3. Swap `<placeholders>` with actual values
4. Append the full line to `<bundle>\STACK\queue.txt` (the folder this file lives in, one level up)
5. Dispatch watches queue.txt, runs each command in a visible cmd.exe window
6. Results appear in `STACK/done.log` (success) or `dispatch/failures.log` (failure)

NO OTHER METHOD. No API. No stdin. No sockets. Write one line to queue.txt.

### Key Paths (all bundle-relative)
- Interpreter: `<bundle>\runtime\python.exe` (the bundled runtime)
- Stack Queue: `<bundle>\STACK\queue.txt`
- Done Log: `<bundle>\STACK\done.log`
- Fail Log: `<bundle>\dispatch\failures.log`

### Format
Each line below is: the verb, a double-colon separator, a plain-English
description, the separator again, then the canonical command. (Written out
here instead of as an example line — legend.py parses ANY three-part
double-colon line it finds, including examples.)

## SKILLS

web :: THE ONE WEB LANE — Obscura headless browser (Rust, local binary, no key, no subscription). Fetch any URL as text/markdown/links/HTML, or scrape many URLs at scale. Replaces all other web-skill references :: "<bundle>\tools\obscura\obscura.exe" fetch <url> --dump text (single page) :: "<bundle>\tools\obscura\obscura.exe" scrape <url> --format json (batch/scale) :: stealth build at <bundle>\tools\obscura\stealth\obscura.exe --stealth for scraping at scale
webmcp :: Obscura MCP control plane — 36 browser tools (navigate, snapshot, click, fill, evaluate, extract, tabs, cookies, storage_state, screenshot, pdf) over JSON-RPC at http://127.0.0.1:3366/mcp. THE point of web operation for agents and scripts. Server: "<bundle>\tools\obscura\obscura.exe" mcp --http --port 3366 :: MCP call: POST http://127.0.0.1:3366/mcp with mcp-session-id header (initialize first, then tools/call)
spider :: LEGACY — superseded by web (Obscura). Do not call; kept for reference only :: retired
browse :: Open a URL in your own already-running Chrome session reusing your cookies, read the page text, return it to chat :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\cookie_monster.py" <url> --done-token <token>
agentaila :: Forward a prompt to the AILA desktop window by screen mapping and wait for the reply :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\agentaila.py" --prompt "<message>" --done-token <token>
index :: One-shot registry refresh — walk a root bottom-up, create/refresh every FOLDER.md so any model can find any file by reading the folder card :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\index.py" index <root> --done-token <token>
watch :: Real-time registry daemon — keep every FOLDER.md current as files change inside a root :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\index.py" watch <root> --done-token <token>
