import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  log('Java OK');
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  log('PowCalculator: ' + PC);
  // staticke pole 'a' = singleton
  var inst = PC.a.value;
  log('instance: ' + inst);
  var t0 = Date.now();
  try {
    var r = inst.calculateDeepSeekHashV1Pow("90f8c575a4a4c50fc2879d54568800bf91c7871a7f4109be309d1090e65f3aa7", "a2110a2ef5568cc07a9f", 144000);
    log('RESULT=' + r + '  ms=' + (Date.now()-t0));
  } catch (e) {
    log('ERR: ' + e);
  }
});
