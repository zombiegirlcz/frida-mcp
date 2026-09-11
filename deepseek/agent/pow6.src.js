import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  var f = PC.class.getDeclaredField('a'); f.setAccessible(true);
  var inst = Java.cast(f.get(null), PC);
  function t(label, fn){ var t0=Date.now(); try { log(label+' = '+fn()+' ('+(Date.now()-t0)+'ms)'); } catch(e){ log(label+' ERR '+e); } }
  t('ch,salt,144000', function(){ return inst.nativeCalculateDeepSeekHashV1Pow("20013b972357afc7698e07a7b51a632d1d5b265b5f6dd561a842e67cf69c4351","42fe2d477b3c9a9e63b8",144000); });
  t('ch,salt,1000',   function(){ return inst.nativeCalculateDeepSeekHashV1Pow("20013b972357afc7698e07a7b51a632d1d5b265b5f6dd561a842e67cf69c4351","42fe2d477b3c9a9e63b8",1000); });
  t('ch,salt,10000',  function(){ return inst.nativeCalculateDeepSeekHashV1Pow("20013b972357afc7698e07a7b51a632d1d5b265b5f6dd561a842e67cf69c4351","42fe2d477b3c9a9e63b8",10000); });
});
