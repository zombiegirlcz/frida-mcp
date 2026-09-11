function hex(arr) {
  return Array.from(new Uint8Array(arr)).map(function(b){return ('0'+b.toString(16)).slice(-2);}).join('');
}
function tryHook() {
  var m = Process.findModuleByName('librscrypto.so');
  if (!m) return false;
  var base = m.base;
  var calls = 0;
  // Keccak permutace / absorb — dump stav (200 B) na vstupu
  Interceptor.attach(base.add(0x1a7e8), {
    onEnter(args) {
      calls++;
      if (calls <= 3) {
        var st = args[0].readByteArray(200);
        send({ t:'keccak', n: calls, x1: args[1].toString(), state: hex(st) });
      }
    }
  });
  // memcpy calls — co se kopiruje
  Interceptor.attach(base.add(0x53170), {
    onEnter(args) {
      var n = args[2].toInt32();
      if (n > 0 && n < 4096) {
        send({ t:'memcpy', n: n, data: hex(args[1].readByteArray(n)) });
      }
    }
  });
  return true;
}
if (tryHook()) send({ t:'hooked', m:'keccak+memcpy' });
else send({ t:'err', m:'librscrypto nenalezeno' });
