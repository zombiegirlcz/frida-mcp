import Java from 'frida-java-bridge';

function log(o) { send(o); }

// najdi vsechny moduly a jejich TLS-ish exporty
var mods = Process.enumerateModules().filter(function (m) {
  return /ssl|crypto|httpengine|cronet|ttnet|encrypt|conscrypt/i.test(m.name);
});
mods.forEach(function (m) {
  var ex = m.enumerateExports().filter(function (e) {
    return /^(SSL_|TLS_|BoringSSL|CRYPTO_|EVP_|X509_|ssl_)/.test(e.name) ||
           /ssl|tls/i.test(e.name);
  }).map(function (e) { return e.name; });
  log({ t: 'module', name: m.name, path: m.path, size: m.size, nTlsExports: ex.length, sample: ex.slice(0, 12) });
});

// Java stack trace pri nove SSL session
Java.perform(function () {
  var kw = ['okhttp', 'OkHttp', 'ttnet', 'TTNet', 'retrofit', 'Retrofit', 'ktor', 'Ktor', 'cronet', 'Cronet', 'TTTransfer'];
  var seen = {};
  var CL = Java.use('java.lang.ClassLoader');
  CL.loadClass.overload('java.lang.String').implementation = function (name) {
    for (var i = 0; i < kw.length; i++) {
      if (name.indexOf(kw[i]) >= 0 && !seen[name]) { seen[name] = 1; log({ t: 'cls', n: name }); break; }
    }
    return this.loadClass(name);
  };
  log({ t: 'hook', m: 'ClassLoader.loadClass hooked' });
});
