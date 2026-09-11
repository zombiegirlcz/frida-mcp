(() => {
  // agent/pow_verify.src.js
  function hex(arr) {
    return Array.from(new Uint8Array(arr)).map(function(b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }
  function tryHook() {
    var m = Process.findModuleByName("librscrypto.so");
    if (!m) return false;
    var base = m.base;
    var n = 0;
    Interceptor.attach(base.add(108520), {
      onEnter(args) {
        this.msg = args[0].readByteArray(40);
        this.ptr = args[0];
      },
      onLeave(ret) {
        n++;
        if (n <= 3) {
          send({ t: "verify", n, msg: hex(this.msg), digest: hex(this.ptr.readByteArray(32)) });
        }
      }
    });
    return true;
  }
  if (tryHook()) send({ t: "hooked", m: "verify" });
  else send({ t: "err", m: "nope" });
})();
