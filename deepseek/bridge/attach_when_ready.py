import sys, time, frida
pkg, proc_name, script_path = sys.argv[1], sys.argv[2], sys.argv[3]
wait = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
pid = None
deadline = time.time() + 25
while time.time() < deadline:
    for p in dev.enumerate_processes():
        if p.name == proc_name:
            pid = p.pid; break
    if pid is not None:
        break
    time.sleep(0.15)
if pid is None:
    print("[!] proces nenalezen:", proc_name); sys.exit(1)
print(f"[*] attach pid={pid} ({proc_name})")
s = dev.attach(pid)
sc = s.create_script(open(script_path).read())
sc.on("message", lambda m, d: print("MSG:", m, flush=True))
sc.load()
time.sleep(wait)
s.detach()
print("[*] done")
