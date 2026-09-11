import sys, time, frida
dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
pkg = sys.argv[1]
script_path = sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
try:
    pid = dev.spawn(pkg)
except Exception as e:
    print("[!] spawn FAIL:", e); sys.exit(1)
print(f"[*] spawned {pkg} pid={pid}")
s = dev.attach(pid)
sc = s.create_script(open(script_path).read())
sc.on("message", lambda m, d: print("MSG:", m))
sc.load()
dev.resume(pid)
print("[*] resumed")
time.sleep(wait)
try:
    s.detach()
except Exception:
    pass
print("[*] done")
