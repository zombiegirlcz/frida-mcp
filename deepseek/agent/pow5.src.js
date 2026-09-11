import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  var inst = Java.cast(PC.class.getDeclaredField('a').value = (function(){var f=PC.class.getDeclaredField('a');f.setAccessible(true);return f.get(null);})(), PC);
  var CH="90f8c575a4a4c50fc2879d54568800bf91c7871a7f4109be309d1090e65f3aa7", SA="a2110a2ef5568cc07a9f", DI=144000;
  function t(label, fn) { var t0=Date.now(); try { log(label + ' = ' + fn() + ' (' + (Date.now()-t0) + 'ms)'); } catch(e){ log(label + ' ERR ' + e); } }
  t('native(ch,salt,di)', function(){ return inst.nativeCalculateDeepSeekHashV1Pow(CH, SA, DI); });
  t('native(salt,ch,di)', function(){ return inst.nativeCalculateDeepSeekHashV1Pow(SA, CH, DI); });
  t('wrap(di,ch,salt)', function(){ return inst.a.overload('long','java.lang.String','java.lang.String').call(inst, Java.use('java.lang.Long').parseLong(String(DI)), CH, SA); });
  t('wrap(di,salt,ch)', function(){ return inst.a.overload('long','java.lang.String','java.lang.String').call(inst, Java.use('java.lang.Long').parseLong(String(DI)), SA, CH); });
});
