import Java from 'frida-java-bridge';
function log(m){ send({t:'log', m: String(m)}); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  var cls = PC.class;
  log('--- declared fields ---');
  cls.getDeclaredFields().forEach(function (f) {
    try { log('  ' + f.getName() + ' : ' + f.getType().getName()); } catch(e){ log('  ?'); }
  });
  log('--- declared methods ---');
  cls.getDeclaredMethods().forEach(function (m) {
    var ps = m.getParameterTypes().map(function(t){return t.getName();}).join(',');
    log('  ' + m.getName() + '(' + ps + ') -> ' + m.getReturnType().getName() + (m.getModifiers() ? ' mod=' + m.getModifiers() : ''));
  });
  log('--- try INSTANCE/a ---');
  ['INSTANCE','a','b','Companion'].forEach(function (n) {
    try {
      var f = cls.getDeclaredField(n); f.setAccessible(true); var v = f.get(null);
      log('  ' + n + ' = ' + v);
    } catch (e) { log('  ' + n + ' neni/err'); }
  });
});
