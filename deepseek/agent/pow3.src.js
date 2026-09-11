import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  var cls = PC.class;
  var f = cls.getDeclaredField('a'); f.setAccessible(true);
  var inst = f.get(null);
  log('inst=' + inst);
  var t0 = Date.now();
  try {
    var r = inst.nativeCalculateDeepSeekHashV1Pow("90f8c575a4a4c50fc2879d54568800bf91c7871a7f4109be309d1090e65f3aa7", "a2110a2ef5568cc07a9f", 144000);
    log('native RESULT=' + r + ' ms=' + (Date.now()-t0));
  } catch (e) { log('native ERR: ' + e); }
});
