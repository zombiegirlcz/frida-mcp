import Java from 'frida-java-bridge';
function log(o){ send(o); }
Java.perform(function () {
  var L = Java.use('la6');
  L.class.getDeclaredMethods().forEach(function (m) {
    if (m.getName() === 'n' || m.getName() === 'o') {
      var ps = m.getParameterTypes().map(function(t){return t.getName();}).join(',');
      log({ t:'sig', name: m.getName()+'('+ps+')' });
    }
  });
});
