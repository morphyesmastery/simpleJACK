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

spider :: Crawl any URL via headless fetch, strip noise, cross-reference the prompt against page content using local Ollama, narrate results :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\morPHYspider.py" --url <url> --prompt "<question>" --done-token <token>
browse :: Open a URL in your own already-running Chrome session reusing your cookies, read the page text, return it to chat :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\cookie_monster.py" <url> --done-token <token>
agentaila :: Forward a prompt to the AILA desktop window by screen mapping and wait for the reply :: "<bundle>\runtime\python.exe" "<bundle>\SKILLS\agentaila.py" --prompt "<message>" --done-token <token>
