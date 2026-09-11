import Java from 'frida-java-bridge';

function log(m) { send({ t: 'log', m: m }); }

log('deepseek probe loaded');
log('Java.available=' + Java.available);

Java.perform(function () {
  log('Java.perform OK');

  var n = 0, sample = [];
  Java.enumerateLoadedClasses({
    onMatch: function (name) {
      if (name.indexOf('okhttp3') === 0) { n++; if (sample.length < 12) sample.push(name); }
      if (name.indexOf('com.deepseek') === 0 && sample.length < 20) sample.push('APP:' + name);
    },
    onComplete: function () { log('okhttp3 loaded=' + n + ' ' + JSON.stringify(sample)); }
  });

  ['okhttp3.OkHttpClient', 'okhttp3.RequestBody', 'okhttp3.ResponseBody',
   'okhttp3.Request', 'okhttp3.Response', 'okhttp3.Interceptor',
   'okhttp3.internal.http2.Http2Connection'].forEach(function (cn) {
    try { Java.use(cn); log('OK   ' + cn); }
    catch (e) { log('MISS ' + cn + ': ' + e.message); }
  });
});
