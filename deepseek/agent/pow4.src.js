import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  var cls = PC.class;
  var f = cls.getDeclaredField('a'); f.setAccessible(true);

  function tryCall(label, inst) {
    try {
      var r = inst.nativeCalculateDeepSeekHashV1Pow("90f8c575a4a4c50fc2879d54568800bf91c7871a7f4109be309d1090e65f3aa7", "a2110a2ef5568cc07a9f", 144000);
      log(label + ' RESULT=' + r);
    } catch (e) { log(label + ' ERR: ' + e); }
  }

  // 1) pres value
  try { var v = PC.a.value; log('PC.a.value = ' + v); if (v) tryCall('via-value', v); } catch(e){ log('value ERR: '+e); }
  // 2) pres cast
  try { var c = Java.cast(f.get(null), PC); log('cast = ' + c); tryCall('via-cast', c); } catch(e){ log('cast ERR: '+e); }
});
