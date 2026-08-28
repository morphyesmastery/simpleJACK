# TAB: morPHYspider | 🕷️ | Web Crawler & Research
# HOOK: /api/spider_crawl | POST | Crawls URL + cross-references prompt against page content
# DEST: tool
# COLOR: gold

"""
morPHYspider.py — LightPanda-powered web crawler and researcher.
Stack-driven. Legend resolves &spider → dispatch runs it in visible cmd window.

Accepts: --url <url> --prompt <query> --done-token <token>
Calls LightPanda in WSL2 to fetch the page with bullshit stripped.
Chunks large pages, cross-references prompt against each chunk using local Ollama.
Synthesizes findings, writes narration JSON to queue, writes done token.

NO TIMEOUTS. This is MorPHYes. We wait.

EVERYONE IS AILA — results speak through the same Piper Alba narration pipe.
The chat interface doesn't know this happened except that it appears on screen.

SimpleTool contract: --done-token required. done_<token>.json written last.
"""

import os
import sys
import json
import time
import subprocess
import re
import argparse
import requests
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════════════════════
# PATHS
# ════════════════════════════════════════════════════════
NARRATION_QUEUE = Path.home() / "Desktop" / "morPHYtrek_RELEASE" / "morphytrek_data" / "queue"
OLLAMA_CHAT = "http://localhost:11434/api/chat"

# ════════════════════════════════════════════════════════
# SANITIZER (same rules as simplejack's narrate)
# ════════════════════════════════════════════════════════
def sanitize_for_ears(text):
    clean = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    clean = clean.replace('```', ' ').replace('`', ' ')
    clean = re.sub(r'[A-Za-z]:\\[^\s"\'<>\[\]]+', 'the file', clean)
    clean = re.sub(r'/[^\s"\'<>]+\.\w{2,5}', 'the file', clean)
    clean = clean.replace('\\', ' then ').replace('/', ' then ')
    for arrow in ('→', '=>', '->', '<-', '←'):
        clean = clean.replace(arrow, ' then ')
    for h in (' - ', ' — ', ' – '):
        clean = clean.replace(h, ' then ')
    clean = clean.replace('#', ' ').replace('*', ' ').replace('|', ' ')
    clean = clean.replace('{{', ' ').replace('}}', ' ')
    clean = clean.replace('[', ' ').replace(']', ' ')
    clean = clean.replace('(', ' ').replace(')', ' ')
    clean = re.sub(r'[<>{}^~|]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = re.sub(r'( then){2,}', ' then ', clean)
    return clean


def narrate(text):
    if not text or not text.strip():
        return
    NARRATION_QUEUE.mkdir(parents=True, exist_ok=True)
    clean = sanitize_for_ears(text)

    MAX_CHUNK = 2000
    chunks = [clean]
    if len(clean) > MAX_CHUNK:
        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+', clean)
        buf = ""
        for s in sentences:
            if len(buf) + len(s) + 1 <= MAX_CHUNK:
                buf = (buf + " " + s).strip()
            else:
                if buf:
                    chunks.append(buf)
                if len(s) > MAX_CHUNK:
                    words = s.split(" ")
                    hard = ""
                    for w in words:
                        if len(hard) + len(w) + 1 <= MAX_CHUNK:
                            hard = (hard + " " + w).strip()
                        else:
                            if hard:
                                chunks.append(hard)
                            hard = w
                    buf = hard
                else:
                    buf = s
        if buf:
            chunks.append(buf)

    for chunk in chunks:
        ts = int(time.time() * 1000000)
        final = NARRATION_QUEUE / f"spider_{ts}.json"
        tmp = final.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"text": chunk, "source": "aila", "engine": "piper"}),
            encoding="utf-8"
        )
        tmp.replace(final)
        time.sleep(0.05)


# ════════════════════════════════════════════════════════
# LIGHTPANDA CALL — NO TIMEOUT
# ════════════════════════════════════════════════════════
def fetch_page(url, wait_ms=5000):
    """Fetch a URL via LightPanda in WSL2. Returns dict with content or error."""
    print(f"[spider] Fetching: {url}")
    
    cmd = (
        f'$HOME/.local/bin/lightpanda fetch "{url}" '
        f'--dump markdown '
        f'--strip-mode "js,ui,css,invisible" '
        f'--wait-ms {wait_ms} '
        f'--json'
    )

    try:
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-c", cmd],
            capture_output=True, text=True,
            timeout=None  # NO TIMEOUT. We wait.
        )
    except Exception as e:
        return {"error": f"LightPanda call failed: {e}"}

    if result.returncode != 0:
        return {"error": f"LightPanda exit {result.returncode}: {result.stderr[:300]}"}

    try:
        data = json.loads(result.stdout.strip())
        return data
    except json.JSONDecodeError:
        return {"error": f"Could not parse output. Raw: {result.stdout[:300]}"}


# ════════════════════════════════════════════════════════
# CHUNKING
# ════════════════════════════════════════════════════════
def chunk_text(text, max_chars=6000):
    """Split text into overlapping chunks at paragraph boundaries."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # If single paragraph is too large, split it
            if len(para) > max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                sub = ""
                for s in sentences:
                    if len(sub) + len(s) + 1 <= max_chars:
                        sub = (sub + " " + s).strip()
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = s
                if sub:
                    current = sub
                else:
                    current = ""
            else:
                current = para
    
    if current:
        chunks.append(current)
    
    return chunks


# ════════════════════════════════════════════════════════
# CROSS-REFERENCE — chunked, equipped model
# ════════════════════════════════════════════════════════
def cross_reference(url, page_content, prompt, model):
    """Chunk page, cross-reference each chunk against prompt, synthesize."""
    chunks = chunk_text(page_content, max_chars=6000)
    total_chunks = len(chunks)
    
    print(f"[spider] Page split into {total_chunks} chunks for {model}")
    narrate(f"Page is {len(page_content)} characters. Processing in {total_chunks} chunks.")

    system_prompt = (
        "You are AILA — MorPHYes Mastery. You are reading part of a web page that was just fetched. "
        "Answer the user's question based ONLY on what is in THIS chunk. "
        "If this chunk doesn't contain relevant information, say 'NOT RELEVANT'. "
        "Do not use your training data. Do not guess. Ground everything in the chunk content."
    )

    findings = []
    
    for i, chunk in enumerate(chunks):
        print(f"[spider] Chunk {i+1}/{total_chunks} ({len(chunk)} chars)")
        
        user_message = (
            f"URL: {url}\n\n"
            f"PAGE CHUNK {i+1} of {total_chunks}:\n{chunk}\n\n"
            f"QUESTION: {prompt}"
        )

        try:
            resp = requests.post(OLLAMA_CHAT, json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "stream": False,
                "options": {"temperature": 0.05, "num_predict": 1024}
            }, timeout=None)  # NO TIMEOUT
            
            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("message", {}).get("content", "").strip()
                if answer and "NOT RELEVANT" not in answer.upper():
                    findings.append(answer)
                    print(f"  → Found relevant info ({len(answer)} chars)")
                else:
                    print(f"  → Not relevant")
            else:
                print(f"  → Ollama error {resp.status_code}")
        except Exception as e:
            print(f"  → Chunk {i+1} error: {e}")

    if not findings:
        errors_seen = total_chunks  # all chunks failed if findings empty
        return f"The model failed to process all {errors_seen} chunks. The page was fetched ({len(page_content)} chars) but the local model could not cross-reference it. The model may be down or the model name may be wrong."

    # Synthesize findings
    if len(findings) == 1:
        return findings[0]
    
    print(f"[spider] Synthesizing {len(findings)} findings...")
    synthesis_prompt = (
        "You are AILA. You just read a web page and found several relevant pieces of information. "
        "Synthesize them into one clear, concise answer for Trent. He is listening by voice. "
        "Be direct. No preamble. Just the answer, grounded in what was found on the page."
    )
    
    findings_text = "\n\n---\n\n".join(
        f"FINDING {i+1}:\n{f}" for i, f in enumerate(findings)
    )
    
    try:
        resp = requests.post(OLLAMA_CHAT, json={
            "model": model,
            "messages": [
                {"role": "system", "content": synthesis_prompt},
                {"role": "user", "content": f"QUESTION: {prompt}\n\n{findings_text}"}
            ],
            "stream": False,
            "options": {"temperature": 0.05, "num_predict": 2048}
        }, timeout=None)
        
        if resp.status_code == 200:
            data = resp.json()
            return data.get("message", {}).get("content", findings[0])
    except Exception as e:
        print(f"  → Synthesis error: {e}")
    
    # Fallback: return all findings
    return "\n\n---\n\n".join(findings)


# ════════════════════════════════════════════════════════
# WRITE DONE TOKEN
# ════════════════════════════════════════════════════════
def write_done_token(token):
    done_file = NARRATION_QUEUE / f"done_{token}.json"
    done_file.write_text(
        json.dumps({
            "text": "morPHYspider complete.",
            "source": "aila",
            "engine": "piper",
            "tool": "morPHYspider",
            "ts": datetime.now().isoformat()
        }),
        encoding="utf-8"
    )
    print(f"[spider] Done token written: {done_file}")


# ════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="morPHYspider — LightPanda web crawler")
    parser.add_argument("--url", required=True, help="URL to crawl")
    parser.add_argument("--prompt", required=True, help="Question to answer from the page")
    parser.add_argument("--done-token", required=True, help="Dispatch token for done signal")
    parser.add_argument("--model", default="josie-9b-aila", help="Ollama model for cross-reference (default: josie-9b-aila)")
    parser.add_argument("--wait-ms", type=int, default=5000, help="Wait time for page load in ms")
    args = parser.parse_args()

    url = args.url
    prompt = args.prompt
    token = args.done_token
    model = args.model

    print(f"\n{'='*60}")
    print(f"  morPHYspider")
    print(f"  URL:    {url}")
    print(f"  Prompt: {prompt[:120]}")
    print(f"  Model:  {model}")
    print(f"  Token:  {token}")
    print(f"{'='*60}\n")

    start_time = time.time()

    # ── NARRATE START ──
    narrate(f"morPHYspider crawling. Looking at the page now.")

    # ── FETCH ──
    data = fetch_page(url, wait_ms=args.wait_ms)

    if "error" in data:
        err_msg = f"morPHYspider could not fetch the page. {data['error']}"
        print(f"[spider] ERROR: {err_msg}")
        narrate(err_msg)
        write_done_token(token)
        sys.exit(1)

    page_content = data.get("content", "")

    if not page_content:
        err_msg = "morPHYspider fetched the page but got no content."
        narrate(err_msg)
        write_done_token(token)
        sys.exit(1)

    content_len = len(page_content)
    http_status = data.get("http_status", 0)
    print(f"[spider] Fetched {content_len} chars, HTTP {http_status}")

    # ── CROSS-REFERENCE (chunked) ──
    answer = cross_reference(url, page_content, prompt, model)

    elapsed = int(time.time() - start_time)

    # ── INJECT TO SCREEN — same pipeline as SimpleJack AILA replies ──
    try:
        inject_url = "http://localhost:8791/api/inject"
        requests.post(inject_url, json={"text": answer}, timeout=5)
        print(f"[spider] Injected result to SimpleJack screen ({len(answer)} chars)")
    except Exception as e:
        print(f"[spider] Inject failed (Piper still narrates): {e}")

    # ── NARRATE RESULT ──
    narration_text = (
        f"morPHYspider result. Your question was: {prompt}. "
        f"After reading the page: {answer}"
    )

    print(f"\n[spider] RESULT ({len(answer)} chars, {elapsed}s):")
    print(answer[:800])
    if len(answer) > 800:
        print("...")

    narrate(narration_text)

    # ── DONE ──
    write_done_token(token)
    print(f"\n[spider] Complete in {elapsed}s. WE WORK HERE.")


if __name__ == "__main__":
    main()
