import Java from 'frida-java-bridge';
function log(o){ send(o); }
Java.perform(function () {
  var L;
  try { L = Java.use('la6'); } catch (e) { log({t:'err', m:'la6: '+e}); return; }
  var methods = L.class.getDeclaredMethods().map(function(m){return m.getName();});
  log({ t:'la6_methods', m: methods });
  ['n','o','a'].forEach(function (mn) {
    try {
      L[mn].overload('java.lang.Object','java.lang.String','java.lang.String').implementation = function (a,b,c) {
        var bn=String(b), bv=String(c);
        if (/pow|ds-|client|device|auth|token/i.test(bn)) log({t:'hdr', n: bn, v: bv});
        return this[mn](a,b,c);
      };
    } catch (e) {}
  });
  // i 2-arg (name, value)
  ['n','o','a'].forEach(function (mn) {
    try {
      L[mn].overload('java.lang.String','java.lang.String').implementation = function (b,c) {
        var bn=String(b), bv=String(c);
        if (/pow|ds-|client|device|auth|token/i.test(bn)) log({t:'hdr2', n: bn, v: bv});
        return this[mn](b,c);
      };
    } catch (e) {}
  });
  log({ t:'hooked', m: 'la6' });
});
