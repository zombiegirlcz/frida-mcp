import Java from 'frida-java-bridge';

function getInst() {
  const PC = Java.use('com.deepseek.crypto.PowCalculator');
  const f = PC.class.getDeclaredField('a');
  f.setAccessible(true);
  return Java.cast(f.get(null), PC);
}

let ready = false;
Java.perform(function () {
  try { getInst(); ready = true; } catch (e) { ready = false; }
});

rpc.exports = {
  ping() { return ready ? 'ready' : 'not-ready'; },
  solve(saltTs, challenge, difficulty) {
    let out = null;
    let err = null;
    Java.perform(function () {
      try {
        out = getInst().nativeCalculateDeepSeekHashV1Pow(String(saltTs), String(challenge), Number(difficulty));
      } catch (e) { err = String(e); }
    });
    if (err !== null) throw new Error(err);
    return Number(out);
  }
};
