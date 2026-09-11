import Java from 'frida-java-bridge';
Java.perform(function () {
  var kw = ['okhttp', 'OkHttp', 'ktor', 'Ktor', 'retrofit', 'Retrofit', 'ttnet', 'TTNet',
            'Http', 'http', 'netty', 'SSL', 'Ssl', 'Network', 'network', 'deepseek', 'DeepSeek'];
  var hits = [];
  Java.enumerateLoadedClasses({
    onMatch: function (n) {
      for (var i = 0; i < kw.length; i++) { if (n.indexOf(kw[i]) >= 0) { hits.push(n); break; } }
    },
    onComplete: function () { send({ t: 'classes', n: hits.length, list: hits }); }
  });
  var mods = Process.enumerateModules().map(function (m) { return m.name; })
    .filter(function (n) { return /ssl|crypto|conscrypt|ttnet|encrypt|flipped|mmkv|wcdb|rs|boringssl/i.test(n); });
  send({ t: 'modules', list: mods });
  // najdi SSL exporty
  ['libssl.so', 'libcrypto.so'].forEach(function (mn) {
    var m = Process.findModuleByName(mn);
    if (!m) { send({ t: 'ssl', m: mn, found: false }); return; }
    var ex = ['SSL_write', 'SSL_read', 'SSL_do_handshake'].map(function (s) {
      var a = m.findExportByName(s); return s + '=' + (a ? a.toString() : 'null');
    });
    send({ t: 'ssl', m: mn, found: true, path: m.path, exports: ex });
  });
});
