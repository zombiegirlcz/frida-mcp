(() => {
  // agent/pow_sym.src.js
  function log(o) {
    send(o);
  }
  Process.enumerateModules().forEach(function(m) {
    if (!/deepseek|rscrypto|encryptor|encrypt/i.test(m.path)) return;
    var hits = m.enumerateExports().filter(function(e) {
      return /pow|deepseek|hash/i.test(e.name);
    }).map(function(e) {
      return e.name + "@" + e.address;
    });
    if (hits.length) log({ t: "sym", lib: m.name, n: hits.length, ex: hits.slice(0, 15) });
    else log({ t: "sym", lib: m.name, n: 0 });
  });
  Process.enumerateModules().forEach(function(m) {
    if (!/rscrypto|encryptor/i.test(m.path)) return;
    var j = m.enumerateExports().filter(function(e) {
      return /^Java_/.test(e.name);
    }).map(function(e) {
      return e.name;
    });
    log({ t: "jni", lib: m.name, n: j.length, ex: j.slice(0, 10) });
  });
})();
