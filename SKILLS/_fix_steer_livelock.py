import sys, shutil, datetime

P = r"C:\Users\daddy\Desktop\simplejack-tauri-COPY\portable\simplejack.py"

OLD_LF = (
    b'            if _STEER["text"]:\n'
    b'                if not full_reasoning and not full_content and not tool_calls_acc:\n'
    b'                    time.sleep(0.2)\n'
    b'                    continue  # hold the steer until there is a breath to keep\n'
)
NEW_LF = (
    b'            if _STEER["text"] and (full_reasoning or full_content or tool_calls_acc):\n'
)

OLD_CRLF = OLD_LF.replace(b"\n", b"\r\n")
NEW_CRLF = NEW_LF.replace(b"\n", b"\r\n")

data = open(P, "rb").read()

n_lf = data.count(OLD_LF)
n_crlf = data.count(OLD_CRLF)
print("OCCURRENCES lf=%d crlf=%d" % (n_lf, n_crlf))

if n_lf == 1 and n_crlf == 0:
    old, new, tag = OLD_LF, NEW_LF, "LF"
elif n_crlf == 1 and n_lf == 0:
    old, new, tag = OLD_CRLF, NEW_CRLF, "CRLF"
else:
    print("ABORT: anchor not exactly once. NO WRITE.")
    sys.exit(1)

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = P + ".bak_steerfix_" + stamp
shutil.copy2(P, bak)

out = data.replace(old, new, 1)
open(P, "wb").write(out)

print("PATCHED (%s). bytes %d -> %d" % (tag, len(data), len(out)))
print("BACKUP: " + bak)
