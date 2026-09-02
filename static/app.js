/* Mini App «Тренажёр ЕГЭ».
   Экраны и оформление перенесены из макета, данные — с сервера.

   Правила, которые здесь важны:
   - сервер является источником правды: экран выбирается только после ответа
     /api/state, до этого висит сплэш (playbook 5.5);
   - подпись initData уходит с каждым запросом (playbook 5.7);
   - в localStorage ключ привязан к Telegram id, иначе второй аккаунт на том же
     телефоне увидит чужие настройки (playbook 5.4);
   - таймер варианта рисуется локально, но сверяется с сервером: время идёт и
     когда приложение закрыто (ТЗ п.9). */
(function () {
  'use strict';

  var tg = (window.Telegram && window.Telegram.WebApp) || null;

  /* ------------------------------------------------------------------ */
  /* Помощники                                                          */
  /* ------------------------------------------------------------------ */
  function h(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2).toLowerCase(), value);
        else if (value === true) node.setAttribute(key, '');
        else node.setAttribute(key, value);
      });
    }
    appendAll(node, children);
    return node;
  }

  function appendAll(node, children) {
    if (children === null || children === undefined || children === false) return;
    if (Array.isArray(children)) {
      children.forEach(function (child) { appendAll(node, child); });
      return;
    }
    node.appendChild(children.nodeType ? children : document.createTextNode(String(children)));
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function pad2(value) {
    return value < 10 ? '0' + value : String(value);
  }

  /** Секунды -> «1:23:45» или «23:45». */
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var rest = seconds % 60;
    return hours > 0
      ? hours + ':' + pad2(minutes) + ':' + pad2(rest)
      : minutes + ':' + pad2(rest);
  }

  /** Секунды -> «3 ч 12 мин» для итогов. */
  function duration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.round((seconds % 3600) / 60);
    if (hours && minutes) return hours + ' ч ' + minutes + ' мин';
    if (hours) return hours + ' ч';
    return Math.max(1, minutes) + ' мин';
  }

  function plural(count, one, few, many) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function formatDate(iso) {
    if (!iso) return '—';
    var date = new Date(iso);
    if (isNaN(date.getTime())) return '—';
    return pad2(date.getDate()) + '.' + pad2(date.getMonth() + 1) + '.' + date.getFullYear();
  }

  function isoDay(date) {
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function daysAgo(count) {
    var date = new Date();
    date.setDate(date.getDate() - count);
    return isoDay(date);
  }

  function accentFor(accuracy) {
    if (accuracy >= 80) return 'var(--green-strong)';
    if (accuracy >= 65) return 'var(--accent)';
    return 'var(--red-strong)';
  }

  function haptic(kind) {
    if (!tg || !tg.HapticFeedback) return;
    try {
      if (kind === 'success' || kind === 'error' || kind === 'warning') {
        tg.HapticFeedback.notificationOccurred(kind);
      } else {
        tg.HapticFeedback.impactOccurred(kind || 'light');
      }
    } catch (e) { /* на старых клиентах метода нет */ }
  }

  /* ------------------------------------------------------------------ */
  /* Локальные настройки                                                */
  /* ------------------------------------------------------------------ */
  var storageKey = 'train_gia_guest';

  function loadPrefs() {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || '{}') || {};
    } catch (e) {
      return {};
    }
  }

  function savePrefs(patch) {
    try {
      var current = loadPrefs();
      Object.keys(patch).forEach(function (key) { current[key] = patch[key]; });
      localStorage.setItem(storageKey, JSON.stringify(current));
    } catch (e) { /* приватный режим — переживём без кеша */ }
  }

  /* ------------------------------------------------------------------ */
  /* Запросы к API                                                      */
  /* ------------------------------------------------------------------ */
  function initData() {
    return (tg && tg.initData) || '';
  }

  function api(path, options) {
    options = options || {};
    var headers = { 'X-Telegram-Init-Data': initData() };
    var config = { method: options.method || 'GET', headers: headers };
    if (options.body) {
      headers['Content-Type'] = 'application/json';
      config.body = JSON.stringify(options.body);
    }
    return fetch(path, config).then(function (response) {
      return response.text().then(function (raw) {
        var data = null;
        try { data = raw ? JSON.parse(raw) : null; } catch (e) { data = null; }
        if (!response.ok) {
          var error = new Error('request failed');
          error.status = response.status;
          error.detail = data && data.detail;
          throw error;
        }
        return data;
      });
    });
  }

  /** Текст ошибки, пригодный для показа. Сервер кладёт подробности в detail. */
  function errorText(error) {
    var detail = error && error.detail;
    if (detail && typeof detail === 'object' && detail.message) return detail.message;
    if (typeof detail === 'string') return detail;
    if (error && error.status === 401) return 'Не удалось подтвердить вход. Переоткройте приложение.';
    return 'Не удалось связаться с сервером. Проверьте связь и попробуйте ещё раз.';
  }

  /* ------------------------------------------------------------------ */
  /* Состояние                                                          */
  /* ------------------------------------------------------------------ */
  var S = {
    tab: 'trainer',
    screen: 'home',
    trainerScreen: 'home',
    status: 'loading',   // loading | ready | error
    boot: null,
    tasks: null,
    session: null,
    picked: null,
    selected: [],
    typed: '',        // ответ вводом (open, digits)
    checked: null,       // {is_correct, correct} после проверки
    result: null,
    reviewPosition: null,
    stats: null,
    profile: null,
    onb: null,          // анкета ученика, пока её заполняют
    sheets: null,       // список шпаргалок по заданиям
    sheet: null,        // открытая шпаргалка
    deck: null,         // состояние колоды карточек
    run: null,          // текущий подход: карточки, позиция, счёт
    learned: null,      // список выученных слов колоды
    dateFrom: '',
    dateTo: '',
    preset: '',
    busy: false
  };

  var dialogs = [];
  var timerHandle = null;
  var timerSync = { remaining: 0, paused: true, at: 0 };

  var dom = {};

  /* ------------------------------------------------------------------ */
  /* Навигация                                                          */
  /* ------------------------------------------------------------------ */
  function go(screen, patch) {
    S.screen = screen;
    if (S.tab === 'trainer') S.trainerScreen = screen;
    if (patch) Object.keys(patch).forEach(function (key) { S[key] = patch[key]; });
    render();
  }

  function setTab(tab) {
    if (S.tab === tab) return;
    // Экран тренажёра запоминаем: уход в статистику и обратно не должен выбрасывать
    // из наполовину решённого варианта.
    if (S.tab === 'trainer') S.trainerScreen = S.screen;
    S.tab = tab;
    savePrefs({ tab: tab });
    if (tab === 'trainer') {
      S.screen = S.trainerScreen || 'home';
      // Уход на другую вкладку в момент загрузки списка обрывал запрос, и по
      // возвращении экран оставался скелетом навсегда. Просим заново.
      if (S.screen === 'cardsLearned' && !S.learned) openLearned();
    }
    if (tab === 'stats') { S.screen = 'stats'; loadStats(); }
    if (tab === 'profile') { S.screen = 'profile'; loadProfile(); }
    if (tab === 'cheats') { S.screen = 'cheatsheets'; loadSheets(); }
    render();
  }

  var BACK_MAP = {
    taskList: 'home',
    countSelect: 'taskList',
    training: 'home',
    result: 'home',
    mistake: 'result',
    variantIntro: 'home',
    variant: 'home',
    variantResult: 'home',
    variantReview: 'variantResult',
    deck: 'home',
    cardsRun: 'deck',
    cardsDone: 'deck',
    cardsLearned: 'deck'
  };

  function canGoBack() {
    if (S.tab === 'cheats') return S.screen === 'cheatsheet';
    return S.tab === 'trainer' && !!BACK_MAP[S.screen];
  }

  function goBack() {
    if (dialogs.length) { closeDialog(); return; }
    if (S.tab === 'cheats') {
      if (S.screen === 'cheatsheet') { S.screen = 'cheatsheets'; S.sheet = null; render(); }
      return;
    }
    var target = BACK_MAP[S.screen];
    if (!target) return;
    if (S.screen === 'training' || S.screen === 'variant') {
      // Выход из решения — не потеря: прогресс уже на сервере (ТЗ п.7, 9).
      refreshBoot();
    }
    if (S.screen === 'result' || S.screen === 'variantResult') refreshBoot();
    go(target);
    // Счётчики колоды меняются каждым ответом: возвращаясь к ней, берём свежие.
    if (target === 'deck' && S.deck) loadDeck(S.deck.id);
  }

  /* ------------------------------------------------------------------ */
  /* Диалоги                                                            */
  /* ------------------------------------------------------------------ */
  function showDialog(config) {
    var overlay = h('div', {
      class: 'overlay',
      onClick: function (event) { if (event.target === overlay) closeDialog(); }
    }, h('div', { class: 'dialog' }, [
      h('div', { class: 'dialog__title', text: config.title }),
      config.text ? h('div', { class: 'dialog__text', text: config.text }) : null,
      h('div', { class: 'dialog__actions' }, (config.actions || []).map(function (action) {
        return h('button', {
          class: 'btn ' + (action.kind === 'primary' ? 'btn--primary'
            : action.kind === 'danger' ? 'btn--danger' : 'btn--ghost'),
          type: 'button',
          onClick: function () {
            closeDialog();
            if (action.onClick) action.onClick();
          }
        }, action.label);
      }))
    ]));
    dom.dialogRoot.appendChild(overlay);
    dialogs.push(overlay);
    updateBackButton();
  }

  function closeDialog() {
    var overlay = dialogs.pop();
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    updateBackButton();
  }

  var toastTimer = null;
  function toast(message) {
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { dom.toast.hidden = true; }, 3200);
  }

  /* ------------------------------------------------------------------ */
  /* Загрузка данных                                                    */
  /* ------------------------------------------------------------------ */
  function boot() {
    S.status = 'loading';
    return api('/api/state').then(function (data) {
      S.boot = data;
      S.status = 'ready';
      // Анкета обязательна: пока она не заполнена, тренажёр не показываем —
      // иначе первый вход уводит мимо неё и данные о ученике не собрать.
      if (data.needs_onboarding) startOnboarding(false);
      if (data.user && data.user.id) {
        storageKey = 'train_gia_' + data.user.id;
        var prefs = loadPrefs();
        if (prefs.dateFrom) S.dateFrom = prefs.dateFrom;
        if (prefs.dateTo) S.dateTo = prefs.dateTo;
        if (prefs.preset) S.preset = prefs.preset;
      }
      hideSplash();
      render();
    }).catch(function (error) {
      S.status = 'error';
      S.errorMessage = errorText(error);
      hideSplash();
      render();
    });
  }

  function refreshBoot() {
    return api('/api/state').then(function (data) {
      S.boot = data;
    }).catch(function () { /* карточка «продолжить» обновится позже */ });
  }

  function loadTasks() {
    if (S.tasks) return Promise.resolve(S.tasks);
    return api('/api/tasks').then(function (data) {
      S.tasks = data;
      return data;
    });
  }

  function loadStats() {
    S.stats = null;
    render();
    var query = [];
    if (S.dateFrom) query.push('date_from=' + encodeURIComponent(S.dateFrom));
    if (S.dateTo) query.push('date_to=' + encodeURIComponent(S.dateTo));
    return api('/api/stats' + (query.length ? '?' + query.join('&') : ''))
      .then(function (data) { S.stats = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  function loadProfile() {
    return api('/api/profile')
      .then(function (data) { S.profile = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  function loadSheets() {
    if (S.sheets) return Promise.resolve(S.sheets);
    return api('/api/cheatsheets')
      .then(function (data) { S.sheets = data; render(); return data; })
      .catch(function (error) { toast(errorText(error)); });
  }

  function openSheet(number) {
    S.screen = 'cheatsheet';
    S.sheet = null;
    render();
    api('/api/cheatsheets/' + number)
      .then(function (data) { S.sheet = data; render(); })
      .catch(function (error) {
        S.screen = 'cheatsheets';
        toast(errorText(error));
        render();
      });
  }

  /* ------------------------------------------------------------------ */
  /* Тренировка                                                         */
  /* ------------------------------------------------------------------ */
  function confirmDiscardThen(action) {
    var unfinished = S.boot && S.boot.unfinished;
    if (!unfinished) { action(); return; }
    var label = unfinished.kind === 'variant'
      ? 'полный вариант (' + unfinished.answered + ' из ' + unfinished.total + ')'
      : '№' + unfinished.task_number + ' (' + unfinished.answered + ' из ' + unfinished.total + ')';
    showDialog({
      title: 'Начать заново?',
      text: 'У вас есть незавершённая тренировка: ' + label +
        '. Если начать новую, прежние ответы не сохранятся и в статистику не попадут.',
      actions: [
        { label: 'Начать новую', kind: 'danger', onClick: action },
        { label: 'Отмена' }
      ]
    });
  }

  function startTraining(number, count) {
    if (S.busy) return;
    S.busy = true;
    render();
    api('/api/training/start', { method: 'POST', body: { number: number, count: count } })
      .then(function (session) {
        S.busy = false;
        S.session = session;
        S.selected = [];
        S.typed = '';
        S.checked = null;
        S.result = null;
        go('training');
        refreshBoot();
      })
      .catch(function (error) {
        S.busy = false;
        render();
        var detail = error.detail;
        if (detail && detail.code === 'not_enough_tasks') {
          showDialog({ title: 'Заданий не хватает', text: detail.message, actions: [{ label: 'Понятно', kind: 'primary' }] });
        } else {
          toast(errorText(error));
        }
      });
  }

  function resumeSession() {
    if (S.busy) return;
    S.busy = true;
    api('/api/session')
      .then(function (data) {
        S.busy = false;
        if (data.finished) {
          S.result = data.result;
          go(data.result.kind === 'variant' ? 'variantResult' : 'result');
          refreshBoot();
          return;
        }
        S.session = data.session;
        loadAnswer(data.session.question);
        S.checked = revealFrom(data.session.question);
        if (data.session.kind === 'variant') {
          applyTimer(data.session.timer);
          go('variant');
        } else {
          go('training');
        }
      })
      .catch(function (error) {
        S.busy = false;
        toast(errorText(error));
        refreshBoot().then(render);
      });
  }

  function revealFrom(question) {
    if (!question || !question.answered || question.correct === undefined) return null;
    return {
      is_correct: question.is_correct,
      correct: question.correct,
      answers: question.answers || []
    };
  }

  /** Ответ ещё не дан целиком — кнопку проверки держим неактивной. */
  function answerIsEmpty(question) {
    if (!question) return true;
    if (question.kind === 'open' || question.kind === 'digits') {
      return !S.typed.trim();
    }
    if (question.kind === 'match') {
      var left = question.match_left || [];
      return S.selected.length !== left.length || S.selected.some(function (v) { return !v; });
    }
    return !S.selected.length;
  }

  /** Пустая расстановка нужной длины — по одной ячейке на каждую позицию слева. */
  function emptyMatch(question) {
    return (question.match_left || []).map(function () { return 0; });
  }

  /** Восстанавливает ответ из данных сервера при возврате к заданию. */
  function loadAnswer(question) {
    S.typed = (question && question.typed) || '';
    if (question && question.kind === 'match') {
      var saved = (question.selected || []).slice();
      S.selected = saved.length === (question.match_left || []).length
        ? saved
        : emptyMatch(question);
    } else {
      S.selected = (question && question.selected) || [];
    }
  }

  function pickMatch(leftIndex, value) {
    if (S.checked) return;
    var question = S.session && S.session.question;
    if (!question) return;
    if (S.selected.length !== (question.match_left || []).length) {
      S.selected = emptyMatch(question);
    }
    // Повторное нажатие снимает выбор — иначе ошибочный тап не отменить.
    S.selected[leftIndex] = S.selected[leftIndex] === value ? 0 : value;
    haptic('light');
    render();
  }

  function toggleOption(index) {
    if (S.checked) return;
    var question = S.session && S.session.question;
    if (!question) return;
    if (question.multi) {
      var at = S.selected.indexOf(index);
      if (at >= 0) S.selected.splice(at, 1);
      else S.selected.push(index);
    } else {
      S.selected = [index];
    }
    haptic('light');
    render();
  }

  function submitAnswer() {
    var question = S.session && S.session.question;
    if (S.busy || answerIsEmpty(question)) return;
    var body = { position: question.position };
    if (question.kind === 'open' || question.kind === 'digits') {
      body.typed = S.typed.trim();
    } else {
      body.selected = S.selected;
    }
    S.busy = true;
    render();
    api('/api/session/answer', {
      method: 'POST',
      body: body
    }).then(function (data) {
      S.busy = false;
      if (S.session.kind === 'variant') {
        markVariantAnswered(question.position);
        goToNextUnanswered();
        return;
      }
      S.checked = { is_correct: data.is_correct, correct: data.correct, answers: data.answers || [] };
      S.session.answered = data.answered !== undefined ? data.answered : S.session.answered + 1;
      if (data.finished) S.pendingResult = data.result;
      haptic(data.is_correct ? 'success' : 'error');
      render();
    }).catch(function (error) {
      S.busy = false;
      render();
      toast(errorText(error));
    });
  }

  function nextQuestion() {
    if (S.pendingResult) {
      S.result = S.pendingResult;
      S.pendingResult = null;
      S.session = null;
      go('result');
      refreshBoot();
      return;
    }
    S.busy = true;
    api('/api/session').then(function (data) {
      S.busy = false;
      if (data.finished) {
        S.result = data.result;
        S.session = null;
        go('result');
        refreshBoot();
        return;
      }
      S.session = data.session;
      S.selected = [];
      S.typed = '';
      S.checked = null;
      render();
    }).catch(function (error) {
      S.busy = false;
      toast(errorText(error));
      render();
    });
  }

  /* ------------------------------------------------------------------ */
  /* Полный вариант                                                     */
  /* ------------------------------------------------------------------ */
  function startVariant() {
    if (S.busy) return;
    S.busy = true;
    render();
    api('/api/variant/start', { method: 'POST' })
      .then(function (session) {
        S.busy = false;
        S.session = session;
        loadAnswer(session.question);
        S.checked = null;
        S.result = null;
        applyTimer(session.timer);
        go('variant');
        refreshBoot();
      })
      .catch(function (error) {
        S.busy = false;
        render();
        var detail = error.detail;
        if (detail && detail.code === 'no_variant') {
          showDialog({ title: 'Вариант пока не собрать', text: detail.message, actions: [{ label: 'Понятно', kind: 'primary' }] });
        } else {
          toast(errorText(error));
        }
      });
  }

  function markVariantAnswered(position) {
    (S.session.nav || []).forEach(function (cell) {
      if (cell.position === position) cell.answered = true;
    });
    S.session.answered = (S.session.nav || []).filter(function (cell) { return cell.answered; }).length;
  }

  function openVariantQuestion(position) {
    if (S.busy) return;
    S.busy = true;
    api('/api/session?position=' + position).then(function (data) {
      S.busy = false;
      if (data.finished) { finishedVariant(data.result); return; }
      S.session = data.session;
      loadAnswer(data.session.question);
      applyTimer(data.session.timer);
      render();
    }).catch(function (error) {
      S.busy = false;
      toast(errorText(error));
    });
  }

  function goToNextUnanswered() {
    var nav = S.session.nav || [];
    var current = S.session.question ? S.session.question.position : 0;
    var next = null;
    for (var i = 0; i < nav.length; i++) {
      var candidate = nav[(current + 1 + i) % nav.length];
      if (!candidate.answered) { next = candidate; break; }
    }
    if (!next) { render(); return; }
    openVariantQuestion(next.position);
  }

  function finishedVariant(result) {
    S.result = result;
    S.session = null;
    stopTimer();
    go('variantResult');
    refreshBoot();
  }

  function finishVariant() {
    showDialog({
      title: 'Завершить вариант?',
      text: 'Ответы уже нельзя будет изменить. Задания без ответа засчитаются как нерешённые.',
      actions: [
        {
          label: 'Завершить',
          kind: 'danger',
          onClick: function () {
            api('/api/session/finish', { method: 'POST' })
              .then(function (data) { finishedVariant(data.result); })
              .catch(function (error) { toast(errorText(error)); });
          }
        },
        { label: 'Продолжить решать' }
      ]
    });
  }

  function askPause() {
    // Текст предупреждения — дословно из ТЗ п.9.
    showDialog({
      title: '⚠️ Остановить время?',
      text: 'Рекомендуем останавливать время только если вам действительно пришлось внезапно '
        + 'отлучиться, а не с целью увеличить время на решение, так как это может повлиять '
        + 'на вашу подготовку.\n\nВы действительно хотите остановить время?',
      actions: [
        {
          label: 'Да, остановить',
          kind: 'danger',
          onClick: function () {
            api('/api/variant/pause', { method: 'POST' })
              .then(function (data) { applyTimer(data.timer); render(); })
              .catch(function (error) { toast(errorText(error)); });
          }
        },
        { label: 'Нет, продолжаю' }
      ]
    });
  }

  function resumeTimer() {
    api('/api/variant/resume', { method: 'POST' })
      .then(function (data) { applyTimer(data.timer); render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  /* --- таймер --- */
  function applyTimer(timer) {
    if (!timer) return;
    timerSync = { remaining: timer.remaining, paused: timer.paused, at: Date.now() };
    if (S.session && S.session.timer) S.session.timer = timer;
    startTimer();
  }

  function currentRemaining() {
    if (timerSync.paused) return timerSync.remaining;
    return timerSync.remaining - (Date.now() - timerSync.at) / 1000;
  }

  function startTimer() {
    stopTimer();
    timerHandle = setInterval(tickTimer, 1000);
    tickTimer();
  }

  function stopTimer() {
    if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
  }

  function tickTimer() {
    if (S.screen !== 'variant') { stopTimer(); return; }
    var left = currentRemaining();
    var node = document.getElementById('timer-value');
    if (node) {
      node.textContent = clock(left);
      node.className = 'timer' + (timerSync.paused ? ' is-paused' : left < 300 ? ' is-low' : '');
    }
    if (left <= 0 && !timerSync.paused) {
      stopTimer();
      syncTimer();
    }
  }

  function syncTimer() {
    return api('/api/variant/timer').then(function (data) {
      if (data.finished) {
        showDialog({
          title: 'Время вышло',
          text: 'Вариант завершён автоматически — как на настоящем экзамене.',
          actions: [{ label: 'Посмотреть результат', kind: 'primary' }]
        });
        finishedVariant(data.result);
        return;
      }
      applyTimer(data.timer);
    }).catch(function () { /* сверимся при следующем обращении */ });
  }

  /* ------------------------------------------------------------------ */
  /* Экраны                                                             */
  /* ------------------------------------------------------------------ */
  function screenHome() {
    var boot = S.boot || {};
    var unfinished = boot.unfinished;
    var page = h('div', { class: 'page' }, [
      h('div', { class: 'h1', text: 'Тренажёр' }),
      h('div', { class: 'sub', text: 'ЕГЭ по русскому языку · задания 1–26' })
    ]);

    if (unfinished) {
      var done = unfinished.total ? Math.round(unfinished.answered * 100 / unfinished.total) : 0;
      var label = unfinished.kind === 'variant'
        ? 'Полный вариант · ' + unfinished.answered + '/' + unfinished.total + ' выполнено'
        : '№' + unfinished.task_number + ' · ' + unfinished.answered + '/' + unfinished.total + ' выполнено';
      page.appendChild(h('div', { class: 'resume' }, [
        h('div', { class: 'resume__label', text: 'Продолжить тренировку' }),
        h('div', { class: 'resume__title', text: label }),
        h('div', { class: 'bar mt-14' }, h('div', { class: 'bar__fill', style: 'width:' + done + '%' })),
        h('button', { class: 'btn btn--primary mt-14', type: 'button', onClick: resumeSession }, 'Продолжить')
      ]));
    }

    page.appendChild(h('button', {
      class: 'card mt-20', type: 'button',
      onClick: function () { confirmDiscardThen(function () { go('variantIntro'); }); }
    }, [
      h('div', { class: 'card__icon card__icon--doc' }, h('i')),
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: 'Решить полный вариант' }),
        h('div', {
          class: 'card__note',
          text: 'Задания 1–26, таймер ' + clock(boot.variant_time_limit || 0)
        })
      ]),
      h('div', { class: 'card__chevron', text: '›' })
    ]));

    page.appendChild(h('button', {
      class: 'card mt-10', type: 'button',
      onClick: function () { loadTasks().then(function () { go('taskList'); }).catch(function (e) { toast(errorText(e)); }); }
    }, [
      h('div', { class: 'card__icon card__icon--grid' }, [h('i'), h('i'), h('i'), h('i')]),
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: 'Тренировать конкретное задание' }),
        h('div', { class: 'card__note', text: 'Выбрать номер и количество вопросов' })
      ]),
      h('div', { class: 'card__chevron', text: '›' })
    ]));

    page.appendChild(h('button', {
      class: 'card mt-10', type: 'button',
      onClick: function () { openDeck(DECK_ACCENTS); }
    }, [
      h('div', { class: 'card__icon card__icon--cards' }, [h('i'), h('i')]),
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: 'Карточки: ударения' }),
        h('div', { class: 'card__note', text: 'Задание №4, по 10 слов за подход' })
      ]),
      h('div', { class: 'card__chevron', text: '›' })
    ]));

    if (!boot.tasks_total) {
      page.appendChild(h('div', { class: 'banner banner--amber' },
        'В базе пока нет заданий. Добавьте их через админ-бота — после этого тренировки станут доступны.'));
    }
    return page;
  }

  function screenTaskList() {
    var list = (S.tasks && S.tasks.tasks) || [];
    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Выберите задание' }),
      h('div', { class: 'stack mt-14' }, list.map(function (task) {
        var empty = !task.available;
        return h('button', {
          class: 'task-row', type: 'button', disabled: empty,
          onClick: function () {
            if (empty) return;
            S.picked = task;
            go('countSelect');
          }
        }, [
          h('div', { class: 'task-row__num', text: task.number }),
          h('div', { class: 'task-row__body' }, [
            h('div', { class: 'task-row__title', text: task.title }),
            h('div', { class: 'task-row__desc', text: task.subtitle })
          ]),
          h('div', {
            class: 'task-row__count',
            text: empty ? 'нет' : task.available
          })
        ]);
      }))
    ]);
  }

  function screenCountSelect() {
    var task = S.picked || {};
    var counts = (S.tasks && S.tasks.counts) || [6, 9, 12, 15];
    return h('div', { class: 'page' }, [
      h('div', { class: 'eyebrow', style: 'color:var(--accent)', text: '№' + task.number }),
      h('div', { class: 'h2', style: 'margin-top:6px', text: task.title }),
      h('div', { class: 'sub', text: task.subtitle }),
      h('div', { class: 'h3', style: 'margin-top:26px', text: 'Сколько заданий решаем?' }),
      h('div', { class: 'count-grid' }, counts.map(function (count) {
        var enough = task.available >= count;
        return h('button', {
          class: 'count', type: 'button', disabled: !enough || S.busy,
          onClick: function () { confirmDiscardThen(function () { startTraining(task.number, count); }); }
        }, [
          h('div', { class: 'count__value', text: count }),
          h('div', {
            class: 'count__word',
            text: enough ? plural(count, 'вопрос', 'вопроса', 'вопросов') : 'недоступно'
          })
        ]);
      })),
      task.available < Math.max.apply(null, counts)
        ? h('div', { class: 'banner banner--amber' },
            'В базе ' + task.available + ' ' + plural(task.available, 'задание', 'задания', 'заданий') +
            ' этого номера. Доступны только варианты, которые в него укладываются.')
        : null
    ]);
  }

  function optionNode(option, question) {
    var picked = S.selected.indexOf(option.index) >= 0;
    var classes = ['option'];
    if (question.multi) classes.push('option--multi');
    var mark = '';
    if (S.checked) {
      var isCorrect = S.checked.correct.indexOf(option.index) >= 0;
      if (isCorrect) { classes.push('is-correct'); mark = '✓'; }
      else if (picked) { classes.push('is-wrong'); mark = '✕'; }
    } else if (picked) {
      classes.push('is-picked');
      mark = question.multi ? '✓' : '•';
    }
    return h('button', {
      class: classes.join(' '), type: 'button',
      onClick: function () { toggleOption(option.index); }
    }, [
      h('div', { class: 'option__mark', text: mark }),
      h('div', { class: 'option__letter', text: option.letter }),
      h('div', { class: 'option__text', text: option.text })
    ]);
  }

  /** Блок ответа. Что рисовать — решает вид задания. */
  function answerArea(question) {
    if (question.kind === 'open' || question.kind === 'digits') return inputArea(question);
    if (question.kind === 'match') return matchArea(question);
    return h('div', { class: 'options' }, question.options.map(function (option) {
      return optionNode(option, question);
    }));
  }

  function inputArea(question) {
    var digits = question.kind === 'digits';
    var classes = ['answer-input'];
    if (S.checked) classes.push(S.checked.is_correct ? 'is-correct' : 'is-wrong');

    var input = h('input', {
      class: classes.join(' '),
      type: 'text',
      value: S.typed,
      placeholder: digits ? 'например 245' : 'впишите ответ',
      // Цифровая клавиатура там, где ответ — только цифры.
      inputmode: digits ? 'numeric' : 'text',
      autocomplete: 'off',
      autocapitalize: 'off',
      autocorrect: 'off',
      spellcheck: 'false',
      readonly: !!S.checked,
      onInput: function (event) {
        // Значение держим в состоянии, но не перерисовываем на каждый символ:
        // это сбросило бы фокус и позицию курсора.
        S.typed = event.target.value;
        var button = document.getElementById('submit-btn');
        if (button) button.disabled = answerIsEmpty(question) || S.busy;
      },
      onKeydown: function (event) {
        if (event.key === 'Enter') { event.preventDefault(); submitAnswer(); }
      }
    });

    return h('div', {}, [
      input,
      h('div', {
        class: 'answer-hint',
        text: digits
          ? 'Только цифры, без пробелов и запятых. Порядок не важен.'
          : 'Одно слово или словосочетание, как в ответе на бланке.'
      })
    ]);
  }

  function matchArea(question) {
    var left = question.match_left || [];
    var right = question.options || [];

    var rows = h('div', { class: 'match-list' }, left.map(function (row, leftIndex) {
      var picked = S.selected[leftIndex] || 0;
      var classes = ['match-row'];
      var verdict = null;
      if (S.checked) {
        var right_ = S.checked.correct[leftIndex];
        var ok = picked === right_;
        classes.push(ok ? 'is-correct' : 'is-wrong');
        verdict = h('div', {
          class: 'match-row__verdict',
          text: ok ? 'верно' : 'верный ответ — ' + right_
        });
      }
      return h('div', { class: classes.join(' ') }, [
        h('div', { class: 'match-row__head' }, [
          h('div', { class: 'match-row__letter', text: row.letter }),
          h('div', { class: 'match-row__text', text: row.text })
        ]),
        h('div', { class: 'match-row__picks' }, right.map(function (option, i) {
          var value = i + 1;
          return h('button', {
            class: 'match-pick' + (picked === value ? ' is-picked' : ''),
            type: 'button',
            disabled: !!S.checked,
            onClick: function () { pickMatch(leftIndex, value); }
          }, value);
        })),
        verdict
      ]);
    }));

    return h('div', {}, [
      rows,
      h('div', { class: 'match-options' }, [
        h('div', { class: 'match-options__title', text: 'Варианты для сопоставления' }),
        h('div', { class: 'match-options__list' }, right.map(function (option, i) {
          return h('div', { class: 'match-option' }, [
            h('div', { class: 'match-option__num', text: (i + 1) + ')' }),
            h('div', {}, option.text)
          ]);
        }))
      ])
    ]);
  }

  /** Как показать правильный ответ в вердикте — тоже зависит от вида. */
  function correctLabel(question) {
    if (!S.checked) return '';
    if (question.kind === 'open' || question.kind === 'digits') {
      return (S.checked.answers || []).join(' или ');
    }
    if (question.kind === 'match') {
      return (S.checked.correct || []).map(function (value, i) {
        var row = (question.match_left || [])[i];
        return (row ? row.letter : i + 1) + '-' + value;
      }).join('  ');
    }
    return lettersFor(question, S.checked.correct);
  }

  function screenTraining() {
    var session = S.session;
    if (!session || !session.question) return screenLoading();
    var question = session.question;
    var done = session.total ? Math.round(session.answered * 100 / session.total) : 0;
    var answeredNow = !!S.checked;

    return h('div', { class: 'page' }, [
      h('div', { class: 'q-head' }, [
        h('div', { class: 'q-label', text: 'Вопрос ' + (question.position + 1) + ' из ' + session.total }),
        h('div', { class: 'q-done', text: session.answered + '/' + session.total })
      ]),
      h('div', { class: 'bar mt-10' }, h('div', { class: 'bar__fill', style: 'width:' + done + '%' })),
      question.passage ? h('div', { class: 'q-passage', text: question.passage }) : null,
      h('div', { class: 'q-text', text: question.text }),
      question.multi ? h('div', { class: 'q-hint', text: 'Выберите все верные варианты' }) : null,
      answerArea(question),
      answeredNow ? h('div', { class: 'verdict ' + (S.checked.is_correct ? 'verdict--ok' : 'verdict--no') }, [
        h('div', { class: 'verdict__title', text: S.checked.is_correct ? '✅ Верно!' : '❌ Неверно' }),
        S.checked.is_correct ? null : h('div', {
          class: 'verdict__note',
          text: 'Правильный ответ: ' + correctLabel(question)
        })
      ]) : null,
      answeredNow
        ? h('button', { class: 'btn btn--primary mt-20', type: 'button', onClick: nextQuestion },
            S.pendingResult ? 'Показать результат' : 'Следующее задание')
        : h('button', {
            class: 'btn btn--primary mt-20', id: 'submit-btn', type: 'button',
            disabled: answerIsEmpty(question) || S.busy,
            onClick: submitAnswer
          }, S.busy ? 'Проверяем…' : 'Проверить'),
      h('button', { class: 'btn btn--quiet', type: 'button', onClick: goBack },
        'Выйти — прогресс сохранится')
    ]);
  }

  function lettersFor(question, indexes) {
    return (indexes || []).map(function (index) {
      var option = question.options.filter(function (o) { return o.index === index; })[0];
      return option ? option.letter : index + 1;
    }).join(', ');
  }

  function screenVariantIntro() {
    var boot = S.boot || {};
    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Полный вариант' }),
      h('div', { class: 'sub', text: 'Задания №1–26, как на экзамене' }),
      h('div', { class: 'rows' }, [
        h('div', { class: 'row' }, [
          h('span', { class: 'row__key', text: 'Заданий' }),
          h('span', { class: 'row__value', text: '26' })
        ]),
        h('div', { class: 'row__divider' }),
        h('div', { class: 'row' }, [
          h('span', { class: 'row__key', text: 'Время' }),
          h('span', { class: 'row__value', text: duration(boot.variant_time_limit || 0) })
        ]),
        h('div', { class: 'row__divider' }),
        h('div', { class: 'row' }, [
          h('span', { class: 'row__key', text: 'Сочинение №27' }),
          h('span', { class: 'row__value', text: 'не входит' })
        ])
      ]),
      h('div', { class: 'banner banner--amber' },
        'Задания можно пропускать и возвращаться к ним, ответ разрешено менять до конца работы. '
        + 'Таймер идёт, даже если закрыть приложение.'),
      h('button', {
        class: 'btn btn--primary mt-24', type: 'button', disabled: S.busy,
        onClick: startVariant
      }, S.busy ? 'Готовим вариант…' : 'Начать'),
      h('button', { class: 'btn btn--ghost', type: 'button', onClick: function () { go('home'); } }, 'Не сейчас')
    ]);
  }

  function screenVariant() {
    var session = S.session;
    if (!session || !session.question) return screenLoading();
    var question = session.question;
    var nav = session.nav || [];

    if (timerSync.paused) {
      return h('div', { class: 'page' }, [
        h('div', { class: 'empty' }, [
          h('div', { class: 'empty__title', text: 'Время остановлено' }),
          h('div', { class: 'empty__note', text: 'Осталось ' + clock(timerSync.remaining) + '. Задания скрыты, пока таймер на паузе.' })
        ]),
        h('button', { class: 'btn btn--primary mt-24', type: 'button', onClick: resumeTimer }, 'Продолжить решать'),
        h('button', { class: 'btn btn--ghost', type: 'button', onClick: finishVariant }, 'Завершить вариант')
      ]);
    }

    return h('div', { class: 'page' }, [
      h('div', { class: 'timer-bar' }, [
        h('div', { class: 'timer', id: 'timer-value', text: clock(currentRemaining()) }),
        h('div', { class: 'timer-bar__spacer' }),
        h('button', { class: 'timer-btn', type: 'button', onClick: askPause }, 'Пауза'),
        h('button', { class: 'timer-btn', type: 'button', onClick: finishVariant }, 'Завершить')
      ]),
      h('div', { class: 'nav-grid' }, nav.map(function (cell) {
        var classes = ['nav-cell'];
        if (cell.answered) classes.push('is-answered');
        if (cell.position === question.position) classes.push('is-current');
        return h('button', {
          class: classes.join(' '), type: 'button',
          onClick: function () { openVariantQuestion(cell.position); }
        }, cell.number);
      })),
      h('div', { class: 'q-head', style: 'margin-top:20px' }, [
        h('div', { class: 'q-label', text: '№' + question.number + ' · ' + question.title }),
        h('div', { class: 'q-done', text: session.answered + '/' + session.total })
      ]),
      question.passage ? h('div', { class: 'q-passage', text: question.passage }) : null,
      h('div', { class: 'q-text', text: question.text }),
      question.multi ? h('div', { class: 'q-hint', text: 'Выберите все верные варианты' }) : null,
      answerArea(question),
      h('button', {
        class: 'btn btn--primary mt-20', id: 'submit-btn', type: 'button',
        disabled: answerIsEmpty(question) || S.busy,
        onClick: submitAnswer
      }, question.answered ? 'Сохранить и дальше' : 'Ответить и дальше'),
      h('button', {
        class: 'btn btn--quiet', type: 'button',
        onClick: function () { goToNextUnanswered(); }
      }, 'Пропустить')
    ]);
  }

  function screenResult() {
    var result = S.result;
    if (!result) return screenLoading();
    var mistakes = result.review.filter(function (item) { return item.answered && !item.is_correct; });

    return h('div', { class: 'page' }, [
      h('div', { style: 'text-align:center' }, [
        h('div', { class: 'h2', text: '🎉 Тренировка завершена' }),
        h('div', { class: 'sub', text: '№' + result.task_number + ' · ' + result.title }),
        h('div', { class: 'result-score', text: result.accuracy + '%' })
      ]),
      h('div', { class: 'tiles' }, [
        tile(result.total, 'заданий', ''),
        tile(result.correct, 'верных', 'tile--green'),
        tile(result.wrong, 'ошибок', 'tile--red')
      ]),
      mistakes.length ? h('div', { class: 'mt-24' }, [
        h('div', { class: 'h3', text: 'Ваши ошибки' }),
        h('div', { class: 'stack mt-10' }, mistakes.map(function (item) {
          return h('button', {
            class: 'mistake-row', type: 'button',
            onClick: function () { go('mistake', { reviewPosition: item.position }); }
          }, [
            h('div', { class: 'mistake-row__mark', text: '✕' }),
            h('div', { class: 'mistake-row__label', text: 'Вопрос ' + (item.position + 1) + ' · ' + item.text }),
            h('div', { class: 'mistake-row__chevron', text: '›' })
          ]);
        }))
      ]) : h('div', { class: 'banner banner--green' }, 'Ошибок нет — все ответы верные.'),
      h('button', {
        class: 'btn btn--primary mt-24', type: 'button',
        onClick: function () {
          startTraining(result.task_number, result.total);
        }
      }, 'Повторить тренировку'),
      h('button', {
        class: 'btn btn--ghost', type: 'button',
        onClick: function () { go('home'); refreshBoot().then(render); }
      }, 'Вернуться в тренажёр')
    ]);
  }

  function tile(value, label, modifier) {
    return h('div', { class: 'tile ' + (modifier || '') }, [
      h('div', { class: 'tile__value', text: value }),
      h('div', { class: 'tile__label', text: label })
    ]);
  }

  /** Тело разбора: варианты, столбцы соответствия или ничего для заданий с вводом. */
  function reviewBody(item) {
    if (item.kind === 'open' || item.kind === 'digits') {
      return null;   // вариантов нет, всё видно в строках «ваш» и «правильный»
    }

    if (item.kind === 'match') {
      return h('div', { class: 'match-list mt-14' }, (item.match_left || []).map(function (row, i) {
        var yours = (item.selected || [])[i] || 0;
        var right = (item.correct || [])[i];
        var ok = yours === right;
        var option = (item.options || [])[right - 1];
        return h('div', { class: 'match-row ' + (ok ? 'is-correct' : 'is-wrong') }, [
          h('div', { class: 'match-row__head' }, [
            h('div', { class: 'match-row__letter', text: row.letter }),
            h('div', { class: 'match-row__text', text: row.text })
          ]),
          h('div', {
            class: 'match-row__verdict',
            text: ok
              ? 'верно — ' + right + ') ' + (option ? option.text : '')
              : 'вы указали ' + (yours || '—') + ', верно ' + right + ') ' + (option ? option.text : '')
          })
        ]);
      }));
    }

    return h('div', { class: 'stack mt-14' }, (item.options || []).map(function (option) {
      var isCorrect = item.correct.indexOf(option.index) >= 0;
      var isYours = (item.selected || []).indexOf(option.index) >= 0;
      var classes = ['review-option'];
      if (isCorrect) classes.push('is-correct');
      else if (isYours) classes.push('is-yours');
      return h('div', { class: classes.join(' ') }, [
        h('div', { class: 'option__letter', text: option.letter }),
        h('div', { class: 'review-option__text', text: option.text }),
        h('div', {
          class: 'review-option__tag',
          text: isCorrect ? 'верно' : isYours ? 'ваш ответ' : ''
        })
      ]);
    }));
  }

  function screenMistake() {
    var result = S.result;
    var item = result && result.review.filter(function (row) { return row.position === S.reviewPosition; })[0];
    if (!item) return screenLoading();

    return h('div', { class: 'page' }, [
      h('div', { class: 'eyebrow', text: 'Разбор · №' + item.number }),
      item.passage ? h('div', { class: 'q-passage', text: item.passage }) : null,
      h('div', { class: 'q-text', style: 'margin-top:14px', text: item.text }),
      reviewBody(item),
      h('div', { class: 'stack mt-14' }, [
        h('div', {
          class: 'answer-line answer-line--yours',
          text: 'Ваш ответ: ' + (item.yours_label || '—')
        }),
        h('div', {
          class: 'answer-line answer-line--correct',
          text: 'Правильный ответ: ' + item.correct_letters
        })
      ]),
      h('button', {
        class: 'btn btn--ghost mt-20', type: 'button',
        onClick: function () { go(result.kind === 'variant' ? 'variantReview' : 'result'); }
      }, 'Назад к результату')
    ]);
  }

  function screenVariantResult() {
    var result = S.result;
    if (!result) return screenLoading();
    return h('div', { class: 'page' }, [
      h('div', { style: 'text-align:center' }, [
        h('div', { class: 'h2', text: 'Вариант завершён' }),
        h('div', { class: 'sub', text: 'Задания №1–26 · ' + duration(result.time_spent) })
      ]),
      h('div', { class: 'score-grid' }, [
        h('div', { class: 'score-card score-card--accent' }, [
          h('div', { class: 'score-card__value', text: result.raw_score + '/' + result.max_raw_score }),
          h('div', { class: 'score-card__label', text: 'первичный балл' })
        ]),
        h('div', { class: 'score-card' }, [
          h('div', { class: 'score-card__value', text: result.accuracy + '%' }),
          h('div', { class: 'score-card__label', text: 'точность' })
        ])
      ]),
      h('div', { class: 'tiles' }, [
        tile(result.correct, 'верных', 'tile--green'),
        tile(result.wrong, 'ошибок', 'tile--red'),
        tile(result.skipped, 'пропущено', '')
      ]),
      h('button', {
        class: 'btn btn--primary mt-24', type: 'button',
        onClick: function () { go('variantReview'); }
      }, 'Разбор заданий'),
      h('button', {
        class: 'btn btn--ghost', type: 'button',
        onClick: function () { go('home'); refreshBoot().then(render); }
      }, 'Вернуться в тренажёр')
    ]);
  }

  function screenVariantReview() {
    var result = S.result;
    if (!result) return screenLoading();
    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Разбор варианта' }),
      h('div', { class: 'sub', text: 'Нажмите на задание, чтобы посмотреть подробно' }),
      h('div', { class: 'stack mt-14' }, result.review.map(function (item) {
        var mark = !item.answered ? '—' : item.is_correct ? '✓' : '✕';
        return h('button', {
          class: 'mistake-row', type: 'button',
          onClick: function () { go('mistake', { reviewPosition: item.position }); }
        }, [
          h('div', {
            class: 'mistake-row__mark' + (item.is_correct ? ' mistake-row__mark--ok' : ''),
            text: mark
          }),
          h('div', { class: 'mistake-row__label', text: '№' + item.number + ' · ' + item.title }),
          h('div', { class: 'mistake-row__chevron', text: '›' })
        ]);
      }))
    ]);
  }

  function screenStats() {
    var page = h('div', { class: 'page' }, h('div', { class: 'h1', text: 'Статистика' }));

    page.appendChild(h('div', { class: 'filter' }, [
      h('div', { class: 'eyebrow', text: 'Период' }),
      h('div', { class: 'filter__grid' }, [
        h('label', {}, [
          h('div', { class: 'filter__label', text: 'С' }),
          h('input', {
            class: 'filter__input', type: 'date', value: S.dateFrom,
            onChange: function (event) {
              S.dateFrom = event.target.value;
              S.preset = '';
              savePrefs({ dateFrom: S.dateFrom, preset: '' });
              loadStats();
            }
          })
        ]),
        h('label', {}, [
          h('div', { class: 'filter__label', text: 'По' }),
          h('input', {
            class: 'filter__input', type: 'date', value: S.dateTo,
            onChange: function (event) {
              S.dateTo = event.target.value;
              S.preset = '';
              savePrefs({ dateTo: S.dateTo, preset: '' });
              loadStats();
            }
          })
        ])
      ]),
      h('div', { class: 'chips' }, [
        presetChip('7 дней', 7),
        presetChip('30 дней', 30),
        presetChip('Всё время', null)
      ])
    ]));

    if (!S.stats) {
      page.appendChild(h('div', { class: 'skeleton' }, [h('div'), h('div'), h('div')]));
      return page;
    }

    var overall = S.stats.overall;
    if (!overall.total) {
      page.appendChild(h('div', { class: 'empty' }, [
        h('div', { class: 'empty__icon' }, [h('i'), h('i'), h('i')]),
        h('div', { class: 'empty__title', text: 'Здесь появится ваша статистика' }),
        h('div', { class: 'empty__note', text: 'Решите первую тренировку, чтобы начать отслеживать прогресс.' }),
        h('button', {
          class: 'empty__action', type: 'button',
          onClick: function () {
            setTab('trainer');
            loadTasks().then(function () { go('taskList'); });
          }
        }, 'Начать тренировку')
      ]));
      return page;
    }

    page.appendChild(h('div', { class: 'summary' }, [
      h('div', { class: 'summary__row' }, [
        h('div', {}, [
          h('div', { class: 'eyebrow', text: 'Точность' }),
          h('div', { class: 'summary__value', text: overall.accuracy + '%' })
        ]),
        h('div', { class: 'summary__side' }, [
          h('div', { text: 'Решено: ' + overall.total }),
          h('div', { class: 'is-green', text: 'Верно: ' + overall.correct }),
          h('div', { class: 'is-red', text: 'Неверно: ' + overall.wrong })
        ])
      ]),
      h('div', { class: 'bar bar--thick mt-14' },
        h('div', { class: 'bar__fill', style: 'width:' + overall.accuracy + '%;background:var(--green)' }))
    ]));

    var solved = S.stats.tasks.filter(function (task) { return task.total > 0; });
    page.appendChild(h('div', { class: 'h3 mt-24', text: 'По заданиям' }));
    if (!solved.length) {
      page.appendChild(h('div', { class: 'banner banner--amber' }, 'За выбранный период решённых заданий нет.'));
    } else {
      page.appendChild(h('div', { class: 'stack mt-10' }, solved.map(function (task) {
        var color = accentFor(task.accuracy);
        return h('div', { class: 'stat-row' }, [
          h('div', { class: 'stat-row__head' }, [
            h('div', { class: 'stat-row__num', text: '№' + task.number }),
            h('div', { class: 'stat-row__title', text: task.title }),
            h('div', { class: 'stat-row__acc', style: 'color:' + color, text: task.accuracy + '%' })
          ]),
          h('div', { class: 'bar mt-10' },
            h('div', { class: 'bar__fill', style: 'width:' + task.accuracy + '%;background:' + color })),
          h('div', {
            class: 'stat-row__detail',
            text: 'Решено ' + task.total + ' · верно ' + task.correct + ' · неверно ' + task.wrong
          })
        ]);
      })));
    }

    page.appendChild(h('div', { class: 'h3 mt-24', text: 'История полных вариантов' }));
    if (!S.stats.variants.length) {
      page.appendChild(h('div', { class: 'banner banner--amber' }, 'Полные варианты за этот период ещё не решались.'));
    } else {
      page.appendChild(h('div', { class: 'stack mt-10' }, S.stats.variants.map(function (row) {
        return h('button', {
          class: 'history-row', type: 'button',
          onClick: function () { openHistory(row.id); }
        }, [
          h('div', { class: 'history-row__head' }, [
            h('div', {
              class: 'history-row__title',
              text: row.variant_id ? 'Вариант №' + row.variant_id : 'Случайный вариант'
            }),
            h('div', {
              class: 'history-row__score',
              text: row.raw_score + '/' + row.max_raw_score
            })
          ]),
          h('div', { class: 'history-row__meta' }, [
            h('span', { text: formatDate(row.finished_at) }),
            h('span', { text: duration(row.time_spent) }),
            h('span', { text: row.correct + ' ' + plural(row.correct, 'верный', 'верных', 'верных') })
          ])
        ]);
      })));
    }
    return page;
  }

  function presetChip(label, days) {
    return h('button', {
      class: 'chip' + (S.preset === label ? ' is-active' : ''), type: 'button',
      onClick: function () {
        S.preset = label;
        S.dateFrom = days ? daysAgo(days) : '';
        S.dateTo = days ? isoDay(new Date()) : '';
        savePrefs({ preset: label, dateFrom: S.dateFrom, dateTo: S.dateTo });
        loadStats();
      }
    }, label);
  }

  function openHistory(sessionId) {
    api('/api/session/' + sessionId + '/result').then(function (data) {
      S.result = data.result;
      S.tab = 'trainer';
      go('variantResult');
    }).catch(function (error) { toast(errorText(error)); });
  }

  /* ------------------------------------------------------------------ */
  /* Карточки                                                           */
  /* ------------------------------------------------------------------ */
  var DECK_ACCENTS = 'accents';

  // Формулировки согласованы с методикой — менять только вместе с ней.
  var REPEAT_LOCKED = 'Ты сможешь повторить, когда хотя бы у 5 слов истечет таймер. '
    + 'Это самая рабочая методика заучивания';
  var NO_MORE_NEW = 'Ты разобрал все возможные слова';

  function loadDeck(deckId) {
    return api('/api/cards/' + deckId)
      .then(function (data) { S.deck = data; render(); return data; })
      .catch(function (error) { toast(errorText(error)); });
  }

  function openDeck(deckId) {
    S.deck = null;
    S.run = null;
    S.learned = null;
    go('deck');
    loadDeck(deckId);
  }

  /** Список выученных слов. Грузим по открытию: он растёт каждым подходом. */
  function openLearned() {
    if (!S.deck) return;
    S.learned = null;
    go('cardsLearned');
    api('/api/cards/' + S.deck.id + '/learned')
      .then(function (data) { S.learned = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  /** «через 3 ч 20 мин» — сколько осталось до момента из ISO-строки. */
  function untilText(iso) {
    if (!iso) return '';
    var left = new Date(iso).getTime() - Date.now();
    if (!(left > 0)) return '';
    var minutes = Math.ceil(left / 60000);
    if (minutes < 60) return 'через ' + minutes + ' мин';
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return 'через ' + hours + ' ч' + (rest ? ' ' + rest + ' мин' : '');
  }

  function startRun(mode) {
    if (!S.deck) return;
    api('/api/cards/' + S.deck.id + '/session?mode=' + mode)
      .then(function (data) {
        if (!data.cards.length) {
          toast(mode === 'repeat' ? REPEAT_LOCKED : NO_MORE_NEW);
          return;
        }
        S.run = {
          mode: mode,
          total: data.cards.length,
          queue: data.cards.slice(),
          again: [],          // слова, которые вернутся здесь же, в этом подходе
          at: 0,
          round: 1,
          shown: false,       // перевёрнута ли текущая карточка
          shows: 0,
          known: 0,
          unknown: 0,
          // Сколько было выучено до подхода: разницей считаем, сколько закрылось.
          // Так цифра не зависит от того, успели ли долететь ответы сервера.
          learnedBefore: S.deck.learned || 0
        };
        go('cardsRun');
      })
      .catch(function (error) { toast(errorText(error)); });
  }

  function currentCard() {
    if (!S.run) return null;
    return S.run.queue[S.run.at] || null;
  }

  function revealCard() {
    if (!S.run || S.run.shown) return;
    S.run.shown = true;
    render();
  }

  function answerCard(known) {
    var run = S.run;
    var card = currentCard();
    if (!run || !card) return;

    run.shows += 1;
    if (known) run.known += 1; else run.unknown += 1;

    // Отправляем и идём дальше, не дожидаясь ответа сервера: подход не должен
    // спотыкаться о сеть. Ошибку показываем, но карточку не возвращаем.
    var sent = api('/api/cards/' + S.deck.id + '/answer', {
      method: 'POST',
      body: { card: card.key, known: known }
    }).catch(function (error) { toast(errorText(error)); });

    // В «Повторить» слово, которое ученик не вспомнил, возвращается тут же и
    // будет возвращаться, пока он не ответит «Знаю». В основном тренажёре
    // проход один: там «Не знаю» просто откладывает слово на восемь часов.
    if (!known && run.mode === 'repeat') run.again.push(card);

    run.at += 1;
    run.shown = false;

    if (run.at < run.queue.length) { render(); return; }

    if (run.again.length) {
      run.queue = run.again;
      run.again = [];
      run.at = 0;
      run.round += 1;
      render();
      return;
    }

    go('cardsDone');
    // Счётчики колоды берём после того, как записан последний ответ. Иначе два
    // запроса летят наперегонки, и итог подхода показывает на слово меньше.
    sent.then(function () { return loadDeck(S.deck.id); });
  }

  function resetDeck() {
    var deck = S.deck || {};
    showDialog({
      title: 'Сбросить всю колоду?',
      text: 'Будет стёрт весь прогресс: ' + (deck.learned || 0) + ' выученных слов и '
        + (deck.repeat || 0) + ' отложенных вместе с их таймерами. Все '
        + (deck.total || 0) + ' слов снова станут новыми. Вернуть это будет нельзя.',
      actions: [
        { label: 'Отмена', kind: 'ghost' },
        {
          label: 'Стереть всё', kind: 'danger', onClick: function () {
            api('/api/cards/' + S.deck.id + '/reset', { method: 'POST' })
              .then(function () {
                toast('Прогресс сброшен');
                S.learned = null;
                return loadDeck(S.deck.id);
              })
              .catch(function (error) { toast(errorText(error)); });
          }
        }
      ]
    });
  }

  /** Слово по буквам, ударная — отдельным узлом. */
  function stressedNodes(card) {
    return [
      document.createTextNode(card.answer.slice(0, card.stress)),
      h('span', {
        class: 'card-word__stress',
        text: card.answer.charAt(card.stress).toUpperCase()
      }),
      document.createTextNode(card.answer.slice(card.stress + 1))
    ];
  }

  /** Слово с выделенной ударной буквой — крупно, на карточке. */
  function stressedWord(card) {
    return h('div', { class: 'card-word' }, stressedNodes(card));
  }

  function screenDeck() {
    var deck = S.deck;
    if (!deck) return screenLoading();
    var done = deck.total ? Math.round(deck.learned * 100 / deck.total) : 0;

    var waitLine = 'Готово ' + deck.ready + ' из ' + deck.repeat_min;
    var until = untilText(deck.ready_at);
    if (until) waitLine += ', пятое — ' + until;

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: deck.title }),
      h('div', { class: 'sub', text: deck.subtitle }),

      h('div', { class: 'deck-bar' }, [
        h('div', { class: 'deck-bar__fill', style: 'width:' + done + '%' })
      ]),
      h('div', { class: 'deck-stats' }, [
        deckStat(deck.learned, 'выучено'),
        deckStat(deck.repeat, 'на повторе'),
        deckStat(deck.fresh, 'новых')
      ]),

      h('div', { class: 'stack mt-24' }, [
        h('button', {
          class: 'btn btn--primary', type: 'button', disabled: !deck.fresh,
          onClick: function () { startRun('new'); }
        }, deck.fresh ? 'Учить новые слова' : 'Новых слов нет'),
        h('button', {
          class: 'btn ' + (deck.fresh ? 'btn--ghost' : 'btn--primary'), type: 'button',
          disabled: !deck.can_repeat,
          onClick: function () { startRun('repeat'); }
        }, deck.can_repeat ? 'Повторить (' + deck.ready + ')' : 'Повторить'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !deck.learned,
          onClick: openLearned
        }, deck.learned ? 'Выученные слова (' + deck.learned + ')' : 'Выученных слов пока нет'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !deck.learned && !deck.repeat,
          onClick: resetDeck
        }, 'Сбросить прогресс')
      ]),

      !deck.fresh ? h('div', { class: 'banner' }, NO_MORE_NEW) : null,

      // Пока повторение закрыто, ученик должен понимать, чего он ждёт: иначе
      // неработающая кнопка читается как поломка.
      !deck.can_repeat
        ? h('div', { class: 'banner' }, [
            REPEAT_LOCKED,
            deck.repeat ? h('div', { class: 'banner__note', text: waitLine }) : null
          ])
        : h('div', { class: 'banner' },
            'Готово к повтору: ' + deck.ready + '. В подходе до ' + deck.size + ' слов.')
    ]);
  }

  function deckStat(value, label) {
    return h('div', { class: 'deck-stat' }, [
      h('div', { class: 'deck-stat__value', text: String(value) }),
      h('div', { class: 'deck-stat__label', text: label })
    ]);
  }

  function screenCardsRun() {
    var run = S.run;
    var card = currentCard();
    if (!run || !card) return screenLoading();

    var counter = run.round === 1
      ? (run.at + 1) + ' / ' + run.total
      : 'осталось ' + (run.queue.length - run.at);

    return h('div', { class: 'page' }, [
      h('div', { class: 'card-top' }, [
        h('div', { class: 'card-top__count', text: counter }),
        h('div', { class: 'card-top__group', text: card.group })
      ]),

      run.round > 1
        ? h('div', { class: 'card-round', text: 'Возвращаем слова, которые не дались' })
        : null,

      h('div', { class: 'flashcard' + (run.shown ? ' is-open' : '') }, [
        run.shown
          ? stressedWord(card)
          : h('div', { class: 'card-word card-word--quiet', text: card.word }),
        run.shown && card.hint
          ? h('div', { class: 'card-hint', text: card.hint })
          : null,
        !run.shown
          ? h('div', { class: 'card-tip', text: 'Вспомни, где ударение' })
          : null
      ]),

      run.shown
        ? h('div', { class: 'card-actions' }, [
            h('button', {
              class: 'btn btn--ghost', type: 'button',
              onClick: function () { answerCard(false); }
            }, 'Не знаю'),
            h('button', {
              class: 'btn btn--primary', type: 'button',
              onClick: function () { answerCard(true); }
            }, 'Знаю')
          ])
        : h('button', {
            class: 'btn btn--primary mt-24', type: 'button', onClick: revealCard
          }, 'Показать ударение')
    ]);
  }

  function screenCardsDone() {
    var run = S.run || { mode: 'new', total: 0, shows: 0, known: 0, unknown: 0, learnedBefore: 0 };
    var deck = S.deck || {};
    var closed = Math.max(0, (deck.learned || 0) - run.learnedBefore);
    var repeatMode = run.mode === 'repeat';

    return h('div', { class: 'page' }, [
      h('div', { style: 'text-align:center' }, [
        h('div', { class: 'h2', text: 'Подход пройден' }),
        h('div', { class: 'sub', text: 'Слов в подходе: ' + run.total })
      ]),

      repeatMode
        ? h('div', { class: 'tiles tiles--two' }, [
            tile(closed, 'выучено', closed ? 'tile--green' : ''),
            tile(run.shows, 'показов', '')
          ])
        : h('div', { class: 'tiles tiles--two' }, [
            tile(run.known, 'знаю', 'tile--green'),
            tile(run.unknown, 'не знаю', run.unknown ? 'tile--red' : '')
          ]),

      h('div', { class: 'stack mt-24' }, [
        deck.fresh
          ? h('button', {
              class: 'btn btn--primary', type: 'button',
              onClick: function () { startRun('new'); }
            }, 'Ещё подход')
          : null,
        deck.can_repeat
          ? h('button', {
              class: 'btn ' + (deck.fresh ? 'btn--ghost' : 'btn--primary'), type: 'button',
              onClick: function () { startRun('repeat'); }
            }, 'Повторить (' + deck.ready + ')')
          : null,
        h('button', {
          class: 'btn btn--ghost', type: 'button',
          onClick: function () { go('deck'); loadDeck(S.deck.id); }
        }, 'К колоде')
      ]),

      h('div', { class: 'banner' }, repeatMode
        ? 'Слова, которые ты вспомнил, вернутся через сутки — а после этого уйдут '
          + 'в выученные.'
        : 'Слова, которые не дались, вернутся через 8 часов в разделе «Повторить».')
    ]);
  }

  /** Выученные слова: список только посмотреть, вернуть слово в учёбу нельзя. */
  function screenCardsLearned() {
    var data = S.learned;
    if (!data) return screenLoading();

    if (!data.cards.length) {
      return h('div', { class: 'page' }, [
        h('div', { class: 'empty' }, [
          h('div', { class: 'empty__title', text: 'Пока пусто' }),
          h('div', {
            class: 'empty__note',
            text: 'Сюда попадают слова, которые ты закрыл: сразу — если нажал «Знаю» '
              + 'на новом слове, или после двух повторов по таймеру.'
          })
        ])
      ]);
    }

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Выучено ' + data.learned + ' из ' + data.total }),
      h('div', { class: 'sub', text: 'Сверху те, что закрыты последними' }),
      h('div', { class: 'learned-list' }, data.cards.map(function (card) {
        return h('div', { class: 'learned-item' }, [
          h('div', { class: 'learned-item__word' }, stressedNodes(card)),
          card.hint ? h('div', { class: 'learned-item__hint', text: card.hint }) : null
        ]);
      }))
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Шпаргалки                                                          */
  /* ------------------------------------------------------------------ */
  /**
   * Разбирает подмножество markdown, в котором написаны шпаргалки.
   * Своё, а не библиотека: нужны пять правил, а сторонние скрипты в Mini App
   * тянуть неоткуда — внешние CDN в Telegram блокируются.
   */
  function renderMarkdown(text) {
    var blocks = [];
    var lines = (text || '').split('\n');
    var list = null;      // накопитель пунктов текущего списка
    var listOrdered = false;
    var para = [];        // накопитель строк абзаца

    function flushList() {
      if (!list) return;
      blocks.push(h(listOrdered ? 'ol' : 'ul', { class: 'md-list' }, list.map(function (item) {
        return h('li', { class: 'md-list__item' }, inline(item));
      })));
      list = null;
    }

    function flushPara() {
      if (!para.length) return;
      blocks.push(h('p', { class: 'md-p' }, inline(para.join(' '))));
      para = [];
    }

    /** Жирный текст внутри строки. Остальное показываем как есть. */
    function inline(raw) {
      var nodes = [];
      var rest = String(raw);
      var at;
      while ((at = rest.indexOf('**')) >= 0) {
        var close = rest.indexOf('**', at + 2);
        if (close < 0) break;
        if (at > 0) nodes.push(document.createTextNode(rest.slice(0, at)));
        nodes.push(h('b', { text: rest.slice(at + 2, close) }));
        rest = rest.slice(close + 2);
      }
      if (rest) nodes.push(document.createTextNode(rest));
      return nodes;
    }

    lines.forEach(function (raw) {
      var line = raw.replace(/\s+$/, '');
      var trimmed = line.trim();

      if (!trimmed) { flushList(); flushPara(); return; }

      var heading = /^(#{2,3})\s+(.*)$/.exec(trimmed);
      if (heading) {
        flushList(); flushPara();
        blocks.push(h('div', {
          class: heading[1].length === 2 ? 'md-h2' : 'md-h3'
        }, inline(heading[2])));
        return;
      }

      var note = /^>\s?(.*)$/.exec(trimmed);
      if (note) {
        flushList(); flushPara();
        blocks.push(h('div', { class: 'md-note' }, inline(note[1])));
        return;
      }

      var bullet = /^[-*]\s+(.*)$/.exec(trimmed);
      var numbered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
      if (bullet || numbered) {
        flushPara();
        var ordered = !!numbered;
        if (list && listOrdered !== ordered) flushList();
        if (!list) { list = []; listOrdered = ordered; }
        list.push((bullet || numbered)[1]);
        return;
      }

      // Продолжение пункта списка, перенесённое на новую строку.
      if (list) { list[list.length - 1] += ' ' + trimmed; return; }
      para.push(trimmed);
    });

    flushList();
    flushPara();
    return blocks;
  }

  function screenCheatsheets() {
    var data = S.sheets;
    if (!data) return screenLoading();
    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Чек-листы по заданиям' }),
      h('div', {
        class: 'sub',
        text: 'Как решать, что помнить и где обычно теряют балл. Готово ' +
              data.ready + ' из ' + data.total
      }),
      h('div', { class: 'stack mt-24' }, data.items.map(function (item) {
        return h('button', {
          class: 'sheet-row' + (item.ready ? '' : ' is-empty'), type: 'button',
          onClick: function () { openSheet(item.number); }
        }, [
          h('div', { class: 'sheet-row__num', text: '№' + item.number }),
          h('div', { class: 'sheet-row__body' }, [
            h('div', { class: 'sheet-row__title', text: item.title }),
            h('div', { class: 'sheet-row__sub', text: item.subtitle })
          ]),
          h('div', {
            class: 'sheet-row__mark', text: item.ready ? '›' : 'скоро'
          })
        ]);
      }))
    ]);
  }

  function screenCheatsheet() {
    var sheet = S.sheet;
    if (!sheet) return screenLoading();
    return h('div', { class: 'page' }, [
      h('div', { class: 'eyebrow', style: 'color:var(--accent)', text: '№' + sheet.number }),
      h('div', { class: 'h2', style: 'margin-top:6px', text: sheet.title }),
      h('div', { class: 'sub', text: sheet.subtitle }),
      sheet.body
        ? h('div', { class: 'md mt-24' }, renderMarkdown(sheet.body))
        : h('div', { class: 'banner banner--amber', style: 'margin-top:24px' },
            'Чек-лист по этому заданию ещё пишется. Загляните позже.'),
      h('button', {
        class: 'btn btn--ghost mt-24', type: 'button',
        onClick: function () { S.screen = 'cheatsheets'; S.sheet = null; render(); }
      }, 'К списку заданий')
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Анкета ученика                                                     */
  /* ------------------------------------------------------------------ */
  var ONB_STEPS = ['класс', 'математика', 'предметы', 'цель'];

  /** Сколько предметов по выбору можно взять при выбранной математике: [мин, макс]. */
  function extraRange() {
    var levels = (S.onb.options && S.onb.options.math_levels) || [];
    for (var i = 0; i < levels.length; i++) {
      if (levels[i].key === S.onb.math) return [levels[i].extra_min, levels[i].extra_max];
    }
    return [0, 0];
  }

  /** Человеческая запись лимита: «2» или «1–2». */
  function extraLabel() {
    var range = extraRange();
    return range[0] === range[1] ? String(range[1]) : range[0] + '–' + range[1];
  }

  function onbStepReady() {
    if (S.onb.step === 0) return !!S.onb.grade;
    if (S.onb.step === 1) return !!S.onb.math;
    if (S.onb.step === 2) {
      var range = extraRange();
      return S.onb.subjects.length >= range[0] && S.onb.subjects.length <= range[1];
    }
    return !!S.onb.target;
  }

  function startOnboarding(editing) {
    S.onb = {
      step: 0, grade: null, math: null, subjects: [], target: null,
      options: S.onb ? S.onb.options : null, editing: !!editing, saving: false
    };
    // При правке подставляем то, что уже выбрано: анкета короткая, но переписывать
    // её целиком ради смены одного пункта — раздражает.
    var current = editing && S.profile ? S.profile.onboarding : null;
    if (current) {
      S.onb.grade = current.grade;
      S.onb.math = current.math_level;
      S.onb.subjects = (current.subjects || []).slice();
      S.onb.target = current.target_score;
    }
    S.screen = 'onboarding';
    render();
    if (!S.onb.options) {
      api('/api/onboarding/options')
        .then(function (data) { S.onb.options = data; render(); })
        .catch(function (error) { toast(errorText(error)); });
    }
  }

  function toggleSubject(key) {
    var limit = extraRange()[1];
    var at = S.onb.subjects.indexOf(key);
    if (at >= 0) {
      S.onb.subjects.splice(at, 1);
    } else if (S.onb.subjects.length >= limit) {
      toast('Больше ' + limit + ' ' + plural(limit, 'предмета', 'предметов', 'предметов') +
            ' выбрать нельзя. Снимите один, чтобы выбрать другой.');
      return;
    } else {
      S.onb.subjects.push(key);
    }
    render();
  }

  function pickMath(key) {
    if (S.onb.math === key) return;
    S.onb.math = key;
    // Выбранное не сбрасываем: лимиты у уровней пересекаются, и стирать уже
    // отмеченные предметы из-за смены математики значит заставлять выбирать заново.
    // Обрезаем только то, что не влезает в новый максимум.
    var limit = extraRange()[1];
    if (S.onb.subjects.length > limit) S.onb.subjects = S.onb.subjects.slice(0, limit);
    render();
  }

  function saveOnboarding() {
    if (S.onb.saving) return;
    S.onb.saving = true;
    render();
    api('/api/onboarding', {
      method: 'POST',
      body: {
        grade: S.onb.grade,
        math_level: S.onb.math,
        subjects: S.onb.subjects,
        target_score: S.onb.target
      }
    }).then(function () {
      var editing = S.onb.editing;
      S.onb.saving = false;
      if (editing) {
        S.tab = 'profile';
        S.screen = 'profile';
        loadProfile();
        toast('Анкета обновлена');
      } else {
        S.screen = 'home';
        S.trainerScreen = 'home';
        refreshBoot().then(function () { render(); });
      }
      render();
    }).catch(function (error) {
      S.onb.saving = false;
      toast(errorText(error));
      render();
    });
  }

  function pickCard(active, label, onClick, value) {
    return h('button', {
      class: 'pick' + (active ? ' is-active' : ''), type: 'button', onClick: onClick
    }, value === undefined ? [h('div', { class: 'pick__label', text: label })] : [
      h('div', { class: 'pick__value', text: value }),
      h('div', { class: 'pick__label', text: label })
    ]);
  }

  function pickItem(active, title, note, onClick) {
    return h('button', {
      class: 'pick-item' + (active ? ' is-active' : ''), type: 'button', onClick: onClick
    }, [
      h('div', { class: 'pick-item__mark', text: active ? '✓' : '' }),
      h('div', { class: 'pick-item__body' }, [
        h('div', { class: 'pick-item__title', text: title }),
        note ? h('div', { class: 'pick-item__note', text: note }) : null
      ])
    ]);
  }

  function screenOnboarding() {
    var options = S.onb.options;
    if (!options) return screenLoading();

    var body;
    if (S.onb.step === 0) {
      body = [
        h('div', { class: 'h2', text: 'В каком ты классе?' }),
        h('div', { class: 'sub', text: 'Подберём нагрузку под твой год подготовки' }),
        h('div', { class: 'pick-grid' }, options.grades.map(function (grade) {
          return pickCard(S.onb.grade === grade, 'класс', function () {
            S.onb.grade = grade; render();
          }, grade);
        }))
      ];
    } else if (S.onb.step === 1) {
      body = [
        h('div', { class: 'h2', text: 'Какую математику сдаёшь?' }),
        h('div', { class: 'sub', text: 'Русский язык сдают все — он уже в списке' }),
        h('div', { class: 'pick-list' }, options.math_levels.map(function (level) {
          var note = level.extra_min === level.extra_max
            ? 'плюс ' + level.extra_max + ' ' +
              plural(level.extra_max, 'предмет', 'предмета', 'предметов') + ' по выбору'
            : 'плюс ' + level.extra_min + ' или ' + level.extra_max + ' предмета по выбору';
          return pickItem(S.onb.math === level.key, level.title, note, function () {
            pickMath(level.key);
          });
        }))
      ];
    } else if (S.onb.step === 2) {
      body = [
        h('div', { class: 'h2', text: 'Что сдаёшь ещё?' }),
        h('div', {
          class: 'sub',
          text: 'Выбрано ' + S.onb.subjects.length + ' из ' + extraLabel()
        }),
        h('div', { class: 'pick-list' }, options.subjects.map(function (subject) {
          return pickItem(
            S.onb.subjects.indexOf(subject.key) >= 0, subject.title, '',
            function () { toggleSubject(subject.key); }
          );
        }))
      ];
    } else {
      body = [
        h('div', { class: 'h2', text: 'Какой твой желаемый результат?' }),
        h('div', { class: 'sub', text: 'Сумма баллов за все экзамены' }),
        h('div', { class: 'pick-list' }, options.targets.map(function (target) {
          return pickItem(S.onb.target === target.key, target.title, '', function () {
            S.onb.target = target.key; render();
          });
        }))
      ];
    }

    var last = S.onb.step === ONB_STEPS.length - 1;
    return h('div', { class: 'page' }, [
      h('div', { class: 'onb-steps' }, ONB_STEPS.map(function (name, index) {
        return h('div', {
          class: 'onb-step' + (index === S.onb.step ? ' is-active' : '') +
                 (index < S.onb.step ? ' is-done' : ''),
          text: name
        });
      })),
      h('div', { class: 'onb-body' }, body),
      h('div', { class: 'stack mt-24' }, [
        h('button', {
          class: 'btn btn--primary', type: 'button',
          disabled: !onbStepReady() || S.onb.saving,
          onClick: function () {
            if (last) { saveOnboarding(); return; }
            S.onb.step += 1;
            render();
          }
        }, last ? (S.onb.saving ? 'Сохраняю…' : 'Готово') : 'Далее'),
        S.onb.step > 0 || S.onb.editing
          ? h('button', {
              class: 'btn btn--ghost', type: 'button',
              onClick: function () {
                if (S.onb.step > 0) { S.onb.step -= 1; render(); return; }
                S.screen = 'profile';
                render();
              }
            }, S.onb.step > 0 ? 'Назад' : 'Отмена')
          : null
      ])
    ]);
  }

  function screenProfile() {
    var profile = S.profile;
    if (!profile) return screenLoading();
    var initial = (profile.name || '?').trim().charAt(0).toUpperCase();
    return h('div', { class: 'page' }, [
      h('div', { class: 'profile-head' }, [
        h('div', { class: 'avatar', text: initial }),
        h('div', {}, [
          h('div', { class: 'profile-name', text: profile.name }),
          profile.username ? h('div', { class: 'profile-username', text: '@' + profile.username }) : null
        ])
      ]),
      onboardingRows(profile.onboarding),
      h('div', { class: 'rows' }, [
        row('Тариф', profile.is_pro ? 'PRO' : 'Free'),
        h('div', { class: 'row__divider' }),
        row('Подписка до', profile.plan_until ? formatDate(profile.plan_until) : '—'),
        h('div', { class: 'row__divider' }),
        row('В сервисе с', formatDate(profile.registered_at)),
        h('div', { class: 'row__divider' }),
        row('Решено заданий', String(profile.solved_total)),
        h('div', { class: 'row__divider' }),
        row('Точность', profile.accuracy + '%')
      ]),
      h('div', { class: 'stack', style: 'margin-top:16px' }, [
        h('button', {
          class: 'btn btn--ghost', type: 'button',
          onClick: function () { startOnboarding(true); }
        }, 'Изменить анкету'),
        h('button', {
          class: 'btn btn--primary', type: 'button',
          onClick: function () { toast('Подписки появятся в следующем обновлении.'); }
        }, 'Управление подпиской'),
        h('button', {
          class: 'btn btn--ghost', type: 'button',
          onClick: function () { toast('Тарифы появятся в следующем обновлении.'); }
        }, 'Тарифы')
      ])
    ]);
  }

  function row(key, value) {
    return h('div', { class: 'row' }, [
      h('span', { class: 'row__key', text: key }),
      h('span', { class: 'row__value', text: value })
    ]);
  }

  function onboardingRows(onb) {
    if (!onb || !onb.completed) return null;
    return h('div', { class: 'rows' }, [
      row('Класс', onb.grade + '-й'),
      h('div', { class: 'row__divider' }),
      row('Цель', onb.target_title),
      h('div', { class: 'row__divider' }),
      h('div', { class: 'row row--stack' }, [
        h('span', { class: 'row__key', text: 'Экзамены' }),
        // Списком, а не строкой через запятую: предметов до четырёх, и на узком
        // экране строка переносится посередине названия.
        h('div', { class: 'exam-list' }, onb.exams.map(function (exam) {
          return h('span', { class: 'exam', text: exam });
        }))
      ])
    ]);
  }

  function screenLoading() {
    return h('div', { class: 'skeleton' }, [h('div'), h('div'), h('div')]);
  }

  function screenError() {
    return h('div', { class: 'error' }, [
      h('div', { class: 'error__icon', text: '!' }),
      h('div', { class: 'error__text', text: S.errorMessage || 'Не удалось загрузить данные.' }),
      h('button', { class: 'error__action', type: 'button', onClick: boot }, 'Повторить')
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Отрисовка                                                          */
  /* ------------------------------------------------------------------ */
  var TITLES = {
    home: ['Подготовка к ЕГЭ', 'русский язык'],
    taskList: ['Выбор задания', 'задания 1–26'],
    countSelect: ['Настройка тренировки', 'количество вопросов'],
    training: ['Тренировка', 'ответ нельзя изменить'],
    result: ['Результат', 'разбор ошибок'],
    mistake: ['Разбор', 'правильный ответ'],
    variantIntro: ['Полный вариант', 'перед началом'],
    variant: ['Полный вариант', 'идёт время'],
    variantResult: ['Результат варианта', 'баллы и время'],
    variantReview: ['Разбор варианта', 'задания 1–26'],
    stats: ['Статистика', 'ваш прогресс'],
    profile: ['Профиль', 'аккаунт и подписка'],
    onboarding: ['Знакомство', 'четыре быстрых вопроса'],
    cheatsheets: ['Шпаргалки', 'чек-листы по заданиям'],
    cheatsheet: ['Шпаргалка', 'как решать это задание'],
    deck: ['Карточки', 'запоминаем словами'],
    cardsRun: ['Карточки', 'вспомни и проверь'],
    cardsDone: ['Карточки', 'итог подхода'],
    cardsLearned: ['Выученные слова', 'закрытые карточки']
  };

  var lastViewKey = null;

  /** Что считается «тем же самым видом» для сохранения прокрутки. */
  function viewKey() {
    var parts = [S.status, S.tab, S.screen];
    if (S.session) {
      parts.push(S.session.id);
      if (S.session.question) parts.push(S.session.question.position);
    }
    if (S.screen === 'mistake') parts.push(S.reviewPosition);
    if (S.run) parts.push(S.run.at, S.run.shown, S.run.round);
    if (S.result) parts.push(S.result.id);
    return parts.join('|');
  }

  function render() {
    if (S.status === 'loading') return;

    var title = TITLES[S.screen] || TITLES.home;
    if (S.screen === 'training' && S.session) {
      title = ['Тренировка · №' + S.session.task_number, 'ответ нельзя изменить'];
    }
    if (S.screen === 'cheatsheet' && S.sheet) {
      title = ['Шпаргалка · №' + S.sheet.number, S.sheet.title];
    }
    if (S.screen === 'cardsRun' && S.run) {
      title = S.run.round > 1
        ? ['Карточки · добиваем', 'пока не вспомнишь']
        : ['Карточки · ' + (S.run.at + 1) + '/' + S.run.total, 'вспомни и проверь'];
    }
    dom.title.textContent = title[0];
    dom.sub.textContent = title[1];

    dom.back.hidden = !canGoBack();
    updateBackButton();

    // Пока идёт анкета первого входа, ходить по вкладкам некуда: тренажёр ещё
    // не открыт. При правке из профиля вкладки остаются на месте.
    dom.tabbar.hidden = S.screen === 'onboarding' && !(S.onb && S.onb.editing);

    Array.prototype.forEach.call(dom.tabs, function (tab) {
      tab.classList.toggle('is-active', tab.getAttribute('data-tab') === S.tab);
    });

    // Прокрутку сбрасываем только при переходе на другой экран или к другому
    // заданию. Иначе выбор варианта — он тоже вызывает перерисовку — отбрасывал
    // бы к началу длинного текста, и его приходилось бы пролистывать заново.
    var key = viewKey();
    var keepScroll = key === lastViewKey;
    var savedScroll = dom.screen.scrollTop;
    lastViewKey = key;

    clear(dom.screen);
    var node;
    if (S.status === 'error') node = screenError();
    else if (S.screen === 'onboarding') node = screenOnboarding();
    else if (S.tab === 'stats') node = screenStats();
    else if (S.tab === 'profile') node = screenProfile();
    else if (S.tab === 'cheats') {
      node = S.screen === 'cheatsheet' ? screenCheatsheet() : screenCheatsheets();
    }
    else {
      var screens = {
        home: screenHome,
        taskList: screenTaskList,
        countSelect: screenCountSelect,
        training: screenTraining,
        result: screenResult,
        mistake: screenMistake,
        variantIntro: screenVariantIntro,
        variant: screenVariant,
        variantResult: screenVariantResult,
        variantReview: screenVariantReview,
        deck: screenDeck,
        cardsRun: screenCardsRun,
        cardsDone: screenCardsDone,
        cardsLearned: screenCardsLearned
      };
      node = (screens[S.screen] || screenHome)();
    }
    dom.screen.appendChild(node);
    dom.screen.scrollTop = keepScroll ? savedScroll : 0;

    if (S.screen === 'variant' && !timerSync.paused) startTimer();
    else if (S.screen !== 'variant') stopTimer();
  }

  function updateBackButton() {
    if (!tg || !tg.BackButton) return;
    // Нативная кнопка «назад» всегда доступна: своя может уехать под шапку
    // Telegram и стать ненажимаемой (playbook 5.6).
    try {
      if (dialogs.length || canGoBack()) tg.BackButton.show();
      else tg.BackButton.hide();
    } catch (e) { /* старый клиент */ }
  }

  function hideSplash() {
    var splash = document.getElementById('splash');
    if (splash) splash.remove();
    dom.app.hidden = false;
  }

  /* ------------------------------------------------------------------ */
  /* Запуск                                                             */
  /* ------------------------------------------------------------------ */
  function init() {
    dom.app = document.getElementById('app');
    dom.screen = document.getElementById('screen');
    dom.title = document.getElementById('header-title');
    dom.sub = document.getElementById('header-sub');
    dom.back = document.getElementById('back');
    dom.tabs = document.querySelectorAll('.tab');
    dom.tabbar = document.getElementById('tabbar');
    dom.dialogRoot = document.getElementById('dialog-root');
    dom.toast = document.getElementById('toast');

    dom.back.addEventListener('click', goBack);
    Array.prototype.forEach.call(dom.tabs, function (tab) {
      tab.addEventListener('click', function () { setTab(tab.getAttribute('data-tab')); });
    });

    if (tg) {
      try {
        tg.ready();
        tg.expand();
        if (tg.setHeaderColor) tg.setHeaderColor('#ffffff');
        if (tg.setBackgroundColor) tg.setBackgroundColor('#ffffff');
        if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
        if (tg.BackButton) tg.BackButton.onClick(goBack);
      } catch (e) { /* методы зависят от версии клиента */ }
    }

    // Приложение могло провисеть в фоне: время варианта за это время шло.
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState !== 'visible') return;
      if (S.screen === 'variant') syncTimer();
      else if (S.status === 'ready') refreshBoot().then(render);
    });

    if (!initData()) {
      S.status = 'error';
      S.errorMessage = 'Откройте приложение через Telegram — по прямой ссылке оно не работает.';
      hideSplash();
      render();
      return;
    }

    boot();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
