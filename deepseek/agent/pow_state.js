(() => {
  // agent/pow_state.src.js
  function hex(arr) {
    return Array.from(new Uint8Array(arr)).map(function(b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }
  function tryHook() {
    var m = Process.findModuleByName("librscrypto.so");
    if (!m) return false;
    var base = m.base;
    var calls = 0;
    Interceptor.attach(base.add(108520), {
      onEnter(args) {
        calls++;
        if (calls <= 3) {
          var st = args[0].readByteArray(200);
          send({ t: "keccak", n: calls, x1: args[1].toString(), state: hex(st) });
        }
      }
    });
    Interceptor.attach(base.add(340336), {
      onEnter(args) {
        var n = args[2].toInt32();
        if (n > 0 && n < 4096) {
          send({ t: "memcpy", n, data: hex(args[1].readByteArray(n)) });
        }
      }
    });
    return true;
  }
  if (tryHook()) send({ t: "hooked", m: "keccak+memcpy" });
  else send({ t: "err", m: "librscrypto nenalezeno" });
})();
