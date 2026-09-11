function hex(arr) {
  return Array.from(new Uint8Array(arr)).map(function(b){return ('0'+b.toString(16)).slice(-2);}).join('');
}
function tryHook() {
  var m = Process.findModuleByName('librscrypto.so');
  if (!m) return false;
  var base = m.base;
  if (!base) return false;
  Interceptor.attach(base.add(0x19bc0), {
    onEnter(args) {
      var l1 = args[1].toInt32(), l2 = args[3].toInt32();
      var b1 = (l1 > 0 && l1 < 4096) ? args[0].readByteArray(l1) : null;
      var b2 = (l2 > 0 && l2 < 4096) ? args[2].readByteArray(l2) : null;
      send({ t:'core', arg1len: l1, arg1: b1 ? hex(b1) : null,
             arg2len: l2, arg2: b2 ? hex(b2) : null, diff: args[4].toString() });
    }
  });
  return true;
}
if (tryHook()) {
  send({ t:'hooked', m:'core 0x19bc0' });
} else {
  var n = 0;
  var id = setInterval(function () {
    if (tryHook()) { clearInterval(id); send({ t:'hooked', m:'core (delayed)' }); }
    else if (++n > 60) { clearInterval(id); send({ t:'err', m:'librscrypto nenalezeno' }); }
  }, 500);
}
