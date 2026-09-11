import sys, time, threading, frida
sys.path.insert(0, "/root/frida-mcp")
from bridge import dsui

dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
pid = next(p.pid for p in dev.enumerate_processes() if p.name == "DeepSeek")
print("[*] pid", pid, flush=True)
s = dev.attach(pid)
sc = s.create_script(open(sys.argv[1] if len(sys.argv)>1 else "agent/pow_hook.js").read())
got = []
def on_msg(m, d):
    print("MSG:", m, flush=True)
    if m.get("type") == "send":
        got.append(m["payload"])
sc.on("message", on_msg)
sc.load()
time.sleep(1.0)
print("[*] triggeruji send...", flush=True)
try:
    dsui.send_message("ping")
    print("[*] odeslano", flush=True)
except Exception as e:
    print("[!] send err:", e, flush=True)
time.sleep(15)
s.detach()
print("[*] captured:", len(got), flush=True)
