import Java from 'frida-java-bridge';
function hex(arr) {
  return Array.from(new Uint8Array(arr)).map(function(b){return ('0'+b.toString(16)).slice(-2);}).join('');
}
let inst = null;
Java.perform(function () {
  const PC = Java.use('com.deepseek.crypto.PowCalculator');
  const f = PC.class.getDeclaredField('a'); f.setAccessible(true);
  inst = Java.cast(f.get(null), PC);
});
var base = Process.findModuleByName('librscrypto.so').base;
var n = 0, lastDigest = null, lastMsg = null;
Interceptor.attach(base.add(0x1a7e8), {
  onEnter(args) { this.msg = args[0].readByteArray(48); this.ptr = args[0]; },
  onLeave(ret) { n++; lastDigest = this.ptr.readByteArray(32); lastMsg = this.msg; }
});
rpc.exports = {
  solve(saltTs, challenge, difficulty) {
    let out = null, err = null;
    Java.perform(function () {
      try { out = inst.nativeCalculateDeepSeekHashV1Pow(String(saltTs), String(challenge), Number(difficulty)); }
      catch (e) { err = String(e); }
    });
    if (err) throw new Error(err);
    return Number(out);
  },
  info() { return { n: n, lastMsg: lastMsg ? hex(lastMsg) : null, lastDigest: lastDigest ? hex(lastDigest) : null }; }
};
