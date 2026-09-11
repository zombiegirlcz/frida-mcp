import Java from 'frida-java-bridge';

send({ t: 'log', m: 'bundled agent loaded' });
send({ t: 'log', m: 'Java.available = ' + Java.available });
send({ t: 'log', m: 'Process.arch = ' + Process.arch + ' platform=' + Process.platform });

Java.perform(function () {
  send({ t: 'log', m: 'Java.perform OK' });
  var n = 0, sample = [];
  Java.enumerateLoadedClasses({
    onMatch: function (name) {
      if (name.indexOf('okhttp3') === 0) { n++; if (sample.length < 8) sample.push(name); }
    },
    onComplete: function () {
      send({ t: 'log', m: 'okhttp3 classes=' + n + ' ' + JSON.stringify(sample) });
    }
  });
  // zkus najit nase cilove tridy
  ['com.deepseek.chat'].forEach(function () {});
  try { var C = Java.use('okhttp3.OkHttpClient'); send({ t: 'log', m: 'OkHttpClient resolvable: ' + C }); }
  catch (e) { send({ t: 'log', m: 'OkHttpClient NOT found (normalni u gms): ' + e.message }); }
});
