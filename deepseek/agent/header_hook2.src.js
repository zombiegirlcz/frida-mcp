import Java from 'frida-java-bridge';
function log(o){ send(o); }
Java.perform(function () {
  var L = Java.use('la6');
  L.n.overload('md4','java.lang.String','java.lang.Object').implementation = function (h, name, val) {
    var n = String(name);
    if (/pow|ds-|client|device|auth|token|user|host/i.test(n)) {
      var v;
      try { v = String(val); } catch (e) { v = '<'+e+'>'; }
      log({ t:'hdr', n: n, v: v });
    }
    return this.n(h, name, val);
  };
  log({ t:'hooked', m:'la6.n(md4,String,Object)' });
});
