/* Пик-контроллер — логика окошка. Данные приходят из Python (window.__setState). */
(function(){
  'use strict';

  var $ = function(id){ return document.getElementById(id); };
  var st = null;
  var selPlan = localStorage.getItem('pc_plan') || 'pro';
  var lastFitH = 0;

  /* ---------- мост с Python (локальный HTTP-API) ---------- */
  function post(path, body){
    return fetch('/api/' + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    }).catch(function(){});
  }

  /* ---------- утилиты ---------- */
  function fmtDur(mins){
    if (mins == null) return '';
    if (mins < 1) return 'меньше минуты';
    var h = Math.floor(mins/60), m = mins%60, out = [];
    if (h) out.push(h+' ч');
    if (m) out.push(m+' мин');
    return out.join(' ');
  }
  function minsUntil(epoch){
    if (!epoch) return null;
    return Math.max(0, Math.round((epoch - Date.now()/1000)/60));
  }
  function shortAlias(a){ return a ? a.replace('deepseek-','') : ''; }

  /* ---------- общая отрисовка провайдера ---------- */
  function renderProvider(key, p){
    var sec = $('sec-'+key), pill = $('pill-'+key), line = $('peakline-'+key);
    if (!p || p.status === 'nodata'){
      line.textContent = 'Данные ещё не получены — нажмите «Обновить».';
      return;
    }
    sec.classList.toggle('peak', p.peak);
    sec.classList.toggle('offpeak', !p.peak);
    pill.classList.toggle('peak', p.peak);
    pill.classList.toggle('off', !p.peak);
    pill.querySelector('b').textContent = p.peak ? 'ПИК' : 'не пик';

    var mins = minsUntil(p.next_change_epoch);
    var html = 'Пик: <b>пн–пт ' + p.windows_local.join(' и ') + '</b> (ваше время) · ' +
      (p.peak ? 'закончится' : 'начнётся') + (mins != null ? ' через ' + fmtDur(mins) : '');
    if (!p.peak && p.reason_text) html += ' · <span class="why">' + p.reason_text + '</span>';
    line.innerHTML = html;
  }

  /* ---------- GLM ---------- */
  var PLANS_ORDER = [
    ['lite','Lite'], ['pro','Pro'], ['max','Max'],
    ['tstd','Team Std','Team Standard — место в команде'],
    ['tprem','Team Prem','Team Premium — место в команде']
  ];

  function renderGlm(g){
    renderProvider('glm', g);
    if (!g || !g.plans) return;

    $('planSeg').innerHTML = PLANS_ORDER.map(function(p){
      return '<button data-plan="'+p[0]+'" class="'+(p[0]===selPlan?'active':'')+'"'+(p[2]?' title="'+p[2]+'"':'')+'>'+p[1]+'</button>';
    }).join('');

    var pl = g.plans[selPlan] || g.plans.pro;
    $('plan5h').textContent = pl.h5;
    $('planWeek').textContent = pl.week;
    $('planPrice').textContent = pl.price;
    $('planPriceNote').textContent = pl.note || '';
    $('planTokens').textContent = pl.tokens || '';

    if (g.burn){
      $('chipOff').textContent = g.burn.off_text;
      $('chipPeak').textContent = g.burn.peak_text;
    }

    var rows = '<thead><tr><th>Модель</th><th>вход</th><th>кэш</th><th>выход</th></tr></thead><tbody>' +
      g.models.map(function(m){
        return '<tr><td>'+m.name+'</td><td class="p">'+m['in']+'</td><td class="p">'+m.cached+'</td><td class="p">'+m.out+'</td></tr>';
      }).join('') + '</tbody>';
    $('glmModels').innerHTML = rows;
  }

  /* ---------- DeepSeek ---------- */
  function renderDs(d){
    renderProvider('ds', d);
    if (!d || !d.models || !d.models.length) return;

    var models = d.models;
    var head = '<thead><tr><th></th>' + models.map(function(m){
      return '<th title="'+m.alias+'"><span class="m">'+shortAlias(m.alias)+'</span><em>не-пик</em></th>' +
             '<th title="'+m.alias+'"><span class="m">'+shortAlias(m.alias)+'</span><em>пик</em></th>';
    }).join('') + '</tr></thead>';

    var nRows = models[0].rows.length;
    var body = '<tbody>';
    for (var i = 0; i < nRows; i++){
      body += '<tr><td>'+models[0].rows[i][0]+'</td>' + models.map(function(m){
        return '<td class="p">'+m.rows[i][1]+'</td><td class="p">'+m.rows[i][2]+'</td>';
      }).join('') + '</tr>';
    }
    body += '</tbody>';
    $('dsTable').innerHTML = head + body;
    $('dsNote').textContent = models.map(function(m){
      return shortAlias(m.alias) + ' = ' + (m.version || m.alias);
    }).join(' · ');
  }

  /* ---------- низ и баннер ---------- */
  function renderChrome(s){
    var err = s.any_error;
    $('errBanner').hidden = !err;
    if (err) $('errSub').textContent = s.banner_text || '';
    $('popup').classList.toggle('stale', err);
    $('dot-glm').classList.toggle('err', !s.glm || s.glm.status !== 'ok');
    $('dot-ds').classList.toggle('err', !s.ds || s.ds.status !== 'ok');
    $('updTime').textContent = s.updated_text || '—';
    $('btnRefresh').classList.toggle('busy', !!s.refreshing);
  }

  function render(){
    if (!st) return;
    renderGlm(st.glm);
    renderDs(st.ds);
    renderChrome(st);
    fit();
  }

  /* ---------- подбор высоты окна под содержимое ---------- */
  function fit(){
    var m = Math.ceil($('popup').getBoundingClientRect().height);
    if (performance.now() < 1500) return;
    var maxH = (window.screen && screen.availHeight ? screen.availHeight : 1000) - 12;
    var h = m + 28 + 8; // поля + запас
    h = Math.min(h, maxH);
    if (Math.abs(h - lastFitH) < 3) return;
    lastFitH = h;
    post('fit', {h: h, m: m});
  }

  /* ---------- мост с Python (pull-модель: JS сам забирает состояние) ---------- */
  function applyState(s){
    if (!s || !s.glm) return;
    if (s.opaque) document.documentElement.classList.add('opaque');
    st = s;
    render();
  }

  function pullState(){
    fetch('/api/state').then(function(r){ return r.json(); }).then(applyState).catch(function(){});
  }
  pullState();
  setInterval(pullState, 3000);

  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') post('hide');
  });

  /* курсор покинул окошко — сообщаем в Python (там скрытие с задержкой) */
  document.documentElement.addEventListener('mouseleave', function(){ post('mouse_left'); });

  $('btnRefresh').addEventListener('click', function(){ post('refresh'); });

  $('planSeg').addEventListener('click', function(e){
    var b = e.target.closest('button[data-plan]');
    if (!b) return;
    selPlan = b.dataset.plan;
    localStorage.setItem('pc_plan', selPlan);
    if (st) renderGlm(st.glm);
  });

  /* ---------- часы ---------- */
  function tick(){ $('clock').textContent = new Date().toLocaleTimeString('ru-RU'); }
  tick();
  setInterval(tick, 1000);
  setInterval(function(){ if (st) render(); }, 15000); // пересчёт «через сколько пик»
})();
