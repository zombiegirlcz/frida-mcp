import sys, time, frida
dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
pid = int(sys.argv[1])
script_path = sys.argv[2] if len(sys.argv) > 2 else "agent/probe_bridge.js"
print(f"[*] attach pid={pid} script={script_path}")
s = dev.attach(pid)
sc = s.create_script(open(script_path).read())
sc.on("message", lambda m, d: print("MSG:", m))
sc.load()
time.sleep(3)
s.detach()
print("[*] detached")
