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
    weak: null,         // список слабых слов: что сейчас на повторе
    menu: null,         // меню карточек: обе колоды с прогрессом
    par: null,          // словник паронимов, задание №5
    parRun: null,       // текущий подход по паронимам
    parWeak: null,      // слабые паронимы
    parLearned: null,   // выученные паронимы
    means: null,        // средства выразительности, задание №22
    meansRun: null,     // текущий подход по средствам выразительности
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
      if (S.screen === 'cardsWeak' && !S.weak) openWeak();
      if (S.screen === 'cardsMenu' && !S.menu) openCardsMenu();
      if (S.screen === 'parWeak' && !S.parWeak) openParWeak();
      if (S.screen === 'parLearned' && !S.parLearned) openParLearned();
      if (S.screen === 'means' && !S.means) loadMeans();
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
    cardsMenu: 'home',
    deck: 'cardsMenu',
    par: 'cardsMenu',
    parRun: 'par',
    parDone: 'par',
    parWeak: 'par',
    parLearned: 'par',
    means: 'cardsMenu',
    meansRun: 'means',
    meansDone: 'means',
    cardsRun: 'deck',
    cardsDone: 'deck',
    cardsLearned: 'deck',
    cardsWeak: 'deck'
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
    // Счётчики меняются каждым ответом: возвращаясь, берём свежие.
    if (target === 'deck' && S.deck) loadDeck(S.deck.id);
    if (target === 'par') loadPar();
    if (target === 'means') loadMeans();
    if (target === 'cardsMenu') openCardsMenu();
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
      title: 'Остановить время?',
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
      onClick: openCardsMenu
    }, [
      h('div', { class: 'card__icon card__icon--cards' }, [h('i'), h('i')]),
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: 'Карточки' }),
        h('div', { class: 'card__note', text: 'Ударения и паронимы, задания №4 и №5' })
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
        h('div', { class: 'verdict__title', text: S.checked.is_correct ? 'Верно!' : 'Неверно' }),
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
        h('div', { class: 'h2', text: 'Тренировка завершена' }),
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
      // Обложка на оранжевом градиенте: он отмечает событие, и вариант —
      // единственное событие в тренажёре. На других экранах его нет.
      h('div', { class: 'result-cover' }, [
        h('div', { class: 'result-cover__label', text: 'Вариант завершён' }),
        h('div', { class: 'result-cover__value', text: String(result.raw_score) }),
        h('div', {
          class: 'result-cover__note',
          text: 'из ' + result.max_raw_score + ' первичных · задания №1–26'
        })
      ]),
      h('div', { class: 'score-grid' }, [
        h('div', { class: 'score-card score-card--accent' }, [
          h('div', { class: 'score-card__value', text: result.accuracy + '%' }),
          h('div', { class: 'score-card__label', text: 'точность' })
        ]),
        h('div', { class: 'score-card' }, [
          h('div', { class: 'score-card__value', text: duration(result.time_spent) }),
          h('div', { class: 'score-card__label', text: 'затрачено' })
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
    S.weak = null;
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

  /** Слабые слова: что сейчас на повторе. Таймеры идут — берём свежее. */
  function openWeak() {
    if (!S.deck) return;
    S.weak = null;
    go('cardsWeak');
    api('/api/cards/' + S.deck.id + '/weak')
      .then(function (data) { S.weak = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  /** Повторение открыто не всегда, но кнопка нажимается всегда: молча
   *  неработающая кнопка читается как поломка, а окно объясняет причину. */
  function startRepeat() {
    if (S.deck && !S.deck.can_repeat) {
      showDialog({
        title: 'Ещё рано',
        text: REPEAT_LOCKED,
        actions: [{ label: 'Понятно', kind: 'primary' }]
      });
      return;
    }
    startRun('repeat');
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
                S.weak = null;
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

  /**
   * Кольцо из трёх сегментов — путь слова: 8 часов, 24 часа, выучено.
   * Заполненных сегментов столько, сколько этапов позади, поэтому ученик видит
   * прогресс, не читая ни одной цифры.
   *
   * Рисуется conic-gradient, а не картинкой: колец на экране до двух сотен,
   * и каждое должно быть лёгким.
   */
  function stageRing(filled, tone, label) {
    var stops = [];
    for (var index = 0; index < 3; index++) {
      var from = index * 120;
      var color = index < filled ? tone : 'var(--ring-track)';
      // Сегмент 111 градусов, следом просвет: без него три части сливаются в круг.
      stops.push(color + ' ' + from + 'deg ' + (from + 111) + 'deg');
      stops.push('transparent ' + (from + 111) + 'deg ' + (from + 120) + 'deg');
    }
    return h('div', {
      class: 'ring',
      style: 'background:conic-gradient(' + stops.join(',') + ')'
    }, h('div', { class: 'ring__label', text: label }));
  }

  var STAGES = {
    wait8: { filled: 1, tone: 'var(--ring-8)', label: '8ч' },
    wait24: { filled: 2, tone: 'var(--ring-24)', label: '24ч' }
  };

  /** Строка списка: слово с ударением слева, метка и кольцо справа.
   *  Пояснения здесь нет намеренно: список читают глазами по диагонали,
   *  а подсказка нужна на самой карточке, где её и показывают. */
  function wordRow(card, ring, badge) {
    return h('div', { class: 'word-row' }, [
      h('div', { class: 'word-row__body' }, [
        h('div', { class: 'word-row__word' }, stressedNodes(card))
      ]),
      h('div', { class: 'word-row__side' }, [badge || null, ring])
    ]);
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
          onClick: startRepeat
        }, deck.can_repeat ? 'Повторить (' + deck.ready + ')' : 'Повторить'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !deck.repeat,
          onClick: openWeak
        }, deck.repeat ? 'Слабые слова (' + deck.repeat + ')' : 'Слабых слов пока нет'),
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

      deck.can_repeat
        ? h('div', { class: 'banner' },
            'Готово к повтору: ' + deck.ready + '. В подходе до ' + deck.size + ' слов.')
        : (deck.repeat ? h('div', { class: 'banner' }, waitLine) : null)
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

  /** Слабые слова: что сейчас на повторе, с этапом и готовностью. */
  function screenCardsWeak() {
    var data = S.weak;
    if (!data) return screenLoading();

    if (!data.cards.length) {
      return h('div', { class: 'page' }, [
        h('div', { class: 'empty' }, [
          h('div', { class: 'empty__title', text: 'Слабых слов нет' }),
          h('div', {
            class: 'empty__note',
            text: 'Сюда попадают слова, на которых ты нажал «Не знаю». Пока таких нет — '
              + 'значит, всё, что видел, уже закрыто.'
          })
        ])
      ]);
    }

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'На повторе ' + data.repeat + ' слов' }),
      h('div', { class: 'sub', text: 'Готово к повтору: ' + data.ready }),
      h('div', { class: 'word-list' }, data.cards.map(function (card) {
        var stage = STAGES[card.stage] || STAGES.wait8;
        var ring = stageRing(stage.filled, stage.tone, card.ready ? 'Го!' : stage.label);
        var badge = card.ready
          ? h('span', { class: 'ready-badge', text: 'Можно повторять!' })
          : null;
        return wordRow(card, ring, badge);
      }))
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
      h('div', { class: 'word-list' }, data.cards.map(function (card) {
        return wordRow(card, stageRing(3, 'var(--ring-done)', '✓'), null);
      }))
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Меню карточек                                                      */
  /* ------------------------------------------------------------------ */
  function openCardsMenu() {
    S.menu = null;
    go('cardsMenu');
    // Разделы независимы, поэтому и запроса три: у каждого свой прогресс.
    Promise.all([
      api('/api/cards/' + DECK_ACCENTS),
      api('/api/paronyms'),
      api('/api/means')
    ])
      .then(function (all) {
        S.menu = { accents: all[0], paronyms: all[1], means: all[2] };
        render();
      })
      .catch(function (error) { toast(errorText(error)); });
  }

  function deckRow(deck, tag, onClick) {
    var done = deck.total ? Math.round(deck.learned * 100 / deck.total) : 0;
    var note = tag + ' · выучено ' + deck.learned + ' из ' + deck.total;
    if (deck.ready) note += ' · готово к повтору ' + deck.ready;
    return h('button', { class: 'card mt-10', type: 'button', onClick: onClick }, [
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: deck.title }),
        h('div', { class: 'card__note', text: note }),
        h('div', { class: 'deck-bar deck-bar--slim' }, [
          h('div', { class: 'deck-bar__fill', style: 'width:' + done + '%' })
        ])
      ]),
      h('div', { class: 'card__chevron', text: '›' })
    ]);
  }

  /** Строка средств выразительности. Своя, а не deckRow: там выучено из всего и
   *  полоса прогресса, а здесь нет ни того, ни другого — только точность. */
  function meansRow(data, onClick) {
    var note = 'Задание №22 · ' + (data.answered
      ? 'точность ' + data.accuracy + '% за ' + data.answered + ' '
        + plural(data.answered, 'ответ', 'ответа', 'ответов')
      : 'ещё не начинали');
    return h('button', { class: 'card mt-10', type: 'button', onClick: onClick }, [
      h('div', { class: 'card__body' }, [
        h('div', { class: 'card__title', text: data.title }),
        h('div', { class: 'card__note', text: note })
      ]),
      h('div', { class: 'card__chevron', text: '›' })
    ]);
  }

  function screenCardsMenu() {
    var menu = S.menu;
    if (!menu) return screenLoading();
    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Карточки' }),
      h('div', { class: 'sub', text: 'Три раздела, у каждого свой прогресс' }),
      deckRow(menu.accents, 'Задание №4', function () { openDeck(DECK_ACCENTS); }),
      deckRow(menu.paronyms, 'Задание №5', openPar),
      meansRow(menu.means, openMeans)
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Паронимы, задание №5                                               */
  /* ------------------------------------------------------------------ */
  /* Свой раздел целиком: свои экраны, свои запросы, своя таблица в базе.
     С карточками ударений не пересекается — задания разные, и правка здесь
     не должна доставать до задания №4. */

  var PAR_LOCKED = 'Ты сможешь повторить, когда хотя бы у 5 слов истечет таймер. '
    + 'Это самая рабочая методика заучивания';
  var PAR_NO_MORE_NEW = 'Ты разобрал все возможные слова';

  var PAR_STAGES = {
    wait8: { filled: 1, tone: 'var(--ring-8)', label: '8ч' },
    wait24: { filled: 2, tone: 'var(--ring-24)', label: '24ч' }
  };

  function loadPar() {
    return api('/api/paronyms')
      .then(function (data) { S.par = data; render(); return data; })
      .catch(function (error) { toast(errorText(error)); });
  }

  function openPar() {
    S.par = null;
    S.parRun = null;
    S.parWeak = null;
    S.parLearned = null;
    go('par');
    loadPar();
  }

  function openParWeak() {
    S.parWeak = null;
    go('parWeak');
    api('/api/paronyms/weak')
      .then(function (data) { S.parWeak = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  function openParLearned() {
    S.parLearned = null;
    go('parLearned');
    api('/api/paronyms/learned')
      .then(function (data) { S.parLearned = data; render(); })
      .catch(function (error) { toast(errorText(error)); });
  }

  /** Кнопка повтора нажимается всегда: серая кнопка без объяснения читается
   *  как поломка, а окно называет причину. */
  function startParRepeat() {
    if (S.par && !S.par.can_repeat) {
      showDialog({
        title: 'Ещё рано',
        text: PAR_LOCKED,
        actions: [{ label: 'Понятно', kind: 'primary' }]
      });
      return;
    }
    startParRun('repeat');
  }

  function startParRun(mode) {
    api('/api/paronyms/session?mode=' + mode)
      .then(function (data) {
        if (!data.cards.length) {
          toast(mode === 'repeat' ? PAR_LOCKED : PAR_NO_MORE_NEW);
          return;
        }
        S.parRun = {
          mode: mode,
          prompt: data.prompt,
          total: data.cards.length,
          queue: data.cards.slice(),
          again: [],          // группы, которые вернутся здесь же, в этом подходе
          at: 0,
          round: 1,
          shown: false,
          shows: 0,
          known: 0,
          unknown: 0,
          learnedBefore: (S.par && S.par.learned) || 0
        };
        go('parRun');
      })
      .catch(function (error) { toast(errorText(error)); });
  }

  function currentPar() {
    if (!S.parRun) return null;
    return S.parRun.queue[S.parRun.at] || null;
  }

  function revealPar() {
    if (!S.parRun || S.parRun.shown) return;
    S.parRun.shown = true;
    render();
  }

  function answerPar(known) {
    var run = S.parRun;
    var card = currentPar();
    if (!run || !card) return;

    run.shows += 1;
    if (known) run.known += 1; else run.unknown += 1;

    var sent = api('/api/paronyms/answer', {
      method: 'POST',
      body: { card: card.key, known: known }
    }).catch(function (error) { toast(errorText(error)); });

    // В повторе группа, которую ученик не вспомнил, возвращается тут же и будет
    // возвращаться, пока он не ответит «Знаю». В основном тренажёре проход один.
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

    go('parDone');
    // Счётчики берём после того, как записан последний ответ, иначе итог подхода
    // показывает на группу меньше.
    sent.then(function () { return loadPar(); });
  }

  function resetPar() {
    var par = S.par || {};
    showDialog({
      title: 'Сбросить весь словник?',
      text: 'Будет стёрт весь прогресс: ' + (par.learned || 0) + ' выученных групп и '
        + (par.repeat || 0) + ' слабых вместе с их таймерами. Все '
        + (par.total || 0) + ' групп снова станут новыми. Вернуть это будет нельзя.',
      actions: [
        { label: 'Отмена', kind: 'ghost' },
        {
          label: 'Стереть всё', kind: 'danger', onClick: function () {
            api('/api/paronyms/reset', { method: 'POST' })
              .then(function () {
                toast('Прогресс сброшен');
                S.parWeak = null;
                S.parLearned = null;
                return loadPar();
              })
              .catch(function (error) { toast(errorText(error)); });
          }
        }
      ]
    });
  }

  /** Кольцо этапов словника. Своё, а не общее с ударениями: разделы независимы. */
  function parRing(filled, tone, label) {
    var stops = [];
    for (var index = 0; index < 3; index++) {
      var from = index * 120;
      var color = index < filled ? tone : 'var(--ring-track)';
      stops.push(color + ' ' + from + 'deg ' + (from + 111) + 'deg');
      stops.push('transparent ' + (from + 111) + 'deg ' + (from + 120) + 'deg');
    }
    return h('div', {
      class: 'ring',
      style: 'background:conic-gradient(' + stops.join(',') + ')'
    }, h('div', { class: 'ring__label', text: label }));
  }

  /** Строка списка: только слова группы, значений здесь нет — они на карточке. */
  function parRow(card, ring, badge) {
    return h('div', { class: 'word-row' }, [
      h('div', { class: 'word-row__body' }, [
        h('div', { class: 'word-row__word', text: card.title })
      ]),
      h('div', { class: 'word-row__side' }, [badge || null, ring])
    ]);
  }

  function screenPar() {
    var par = S.par;
    if (!par) return screenLoading();
    var done = par.total ? Math.round(par.learned * 100 / par.total) : 0;

    var waitLine = 'Готово ' + par.ready + ' из ' + par.repeat_min;
    var until = untilText(par.ready_at);
    if (until) waitLine += ', пятое — ' + until;

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: par.title }),
      h('div', { class: 'sub', text: par.subtitle }),

      h('div', { class: 'deck-bar' }, [
        h('div', { class: 'deck-bar__fill', style: 'width:' + done + '%' })
      ]),
      h('div', { class: 'deck-stats' }, [
        deckStat(par.learned, 'выучено'),
        deckStat(par.repeat, 'на повторе'),
        deckStat(par.fresh, 'новых')
      ]),

      h('div', { class: 'stack mt-24' }, [
        h('button', {
          class: 'btn btn--primary', type: 'button', disabled: !par.fresh,
          onClick: function () { startParRun('new'); }
        }, par.fresh ? 'Учить новые слова' : 'Новых слов нет'),
        h('button', {
          class: 'btn ' + (par.fresh ? 'btn--ghost' : 'btn--primary'), type: 'button',
          onClick: startParRepeat
        }, par.can_repeat ? 'Повторить (' + par.ready + ')' : 'Повторить'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !par.repeat,
          onClick: openParWeak
        }, par.repeat ? 'Слабые паронимы (' + par.repeat + ')' : 'Слабых паронимов пока нет'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !par.learned,
          onClick: openParLearned
        }, par.learned ? 'Выученные паронимы (' + par.learned + ')' : 'Выученных пока нет'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !par.learned && !par.repeat,
          onClick: resetPar
        }, 'Сбросить прогресс')
      ]),

      !par.fresh ? h('div', { class: 'banner' }, PAR_NO_MORE_NEW) : null,
      par.can_repeat
        ? h('div', { class: 'banner' },
            'Готово к повтору: ' + par.ready + '. В подходе до ' + par.size + ' групп.')
        : (par.repeat ? h('div', { class: 'banner' }, waitLine) : null)
    ]);
  }

  function screenParRun() {
    var run = S.parRun;
    var card = currentPar();
    if (!run || !card) return screenLoading();

    var counter = run.round === 1
      ? (run.at + 1) + ' / ' + run.total
      : 'осталось ' + (run.queue.length - run.at);

    return h('div', { class: 'page' }, [
      h('div', { class: 'card-top' }, [
        h('div', { class: 'card-top__count', text: counter }),
        h('div', { class: 'card-top__group', text: card.section })
      ]),

      run.round > 1
        ? h('div', { class: 'card-round', text: 'Возвращаем то, что не далось' })
        : null,

      h('div', { class: 'flashcard' + (run.shown ? ' is-open' : '') }, [
        h('div', { class: 'par-words', text: card.title }),
        !run.shown ? h('div', { class: 'card-tip', text: run.prompt }) : null,
        run.shown
          ? h('div', { class: 'par-meanings' }, card.words.map(function (word, index) {
              return h('div', { class: 'par-meaning' }, [
                h('span', { class: 'par-meaning__word', text: word }),
                document.createTextNode(' — ' + card.meanings[index])
              ]);
            }))
          : null
      ]),

      run.shown
        ? h('div', { class: 'card-actions' }, [
            h('button', {
              class: 'btn btn--ghost', type: 'button',
              onClick: function () { answerPar(false); }
            }, 'Не знаю'),
            h('button', {
              class: 'btn btn--primary', type: 'button',
              onClick: function () { answerPar(true); }
            }, 'Знаю')
          ])
        : h('button', {
            class: 'btn btn--primary mt-24', type: 'button', onClick: revealPar
          }, 'Показать')
    ]);
  }

  function screenParDone() {
    var run = S.parRun
      || { mode: 'new', total: 0, shows: 0, known: 0, unknown: 0, learnedBefore: 0 };
    var par = S.par || {};
    var closed = Math.max(0, (par.learned || 0) - run.learnedBefore);
    var repeatMode = run.mode === 'repeat';

    return h('div', { class: 'page' }, [
      h('div', { style: 'text-align:center' }, [
        h('div', { class: 'h2', text: 'Подход пройден' }),
        h('div', { class: 'sub', text: 'Групп в подходе: ' + run.total })
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
        par.fresh
          ? h('button', {
              class: 'btn btn--primary', type: 'button',
              onClick: function () { startParRun('new'); }
            }, 'Ещё подход')
          : null,
        par.can_repeat
          ? h('button', {
              class: 'btn ' + (par.fresh ? 'btn--ghost' : 'btn--primary'), type: 'button',
              onClick: function () { startParRun('repeat'); }
            }, 'Повторить (' + par.ready + ')')
          : null,
        h('button', {
          class: 'btn btn--ghost', type: 'button',
          onClick: function () { go('par'); loadPar(); }
        }, 'К словнику')
      ]),

      h('div', { class: 'banner' }, repeatMode
        ? 'То, что ты вспомнил, вернётся через сутки — и после этого уйдёт в выученные.'
        : 'То, что не далось, вернётся через 8 часов в разделе «Повторить».')
    ]);
  }

  function screenParWeak() {
    var data = S.parWeak;
    if (!data) return screenLoading();

    if (!data.cards.length) {
      return h('div', { class: 'page' }, [
        h('div', { class: 'empty' }, [
          h('div', { class: 'empty__title', text: 'Слабых паронимов нет' }),
          h('div', {
            class: 'empty__note',
            text: 'Сюда попадают группы, на которых ты нажал «Не знаю».'
          })
        ])
      ]);
    }

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'На повторе ' + data.repeat + ' групп' }),
      h('div', { class: 'sub', text: 'Готово к повтору: ' + data.ready }),
      h('div', { class: 'word-list' }, data.cards.map(function (card) {
        var stage = PAR_STAGES[card.stage] || PAR_STAGES.wait8;
        var ring = parRing(stage.filled, stage.tone, card.ready ? 'Го!' : stage.label);
        var badge = card.ready
          ? h('span', { class: 'ready-badge', text: 'Можно повторять!' })
          : null;
        return parRow(card, ring, badge);
      }))
    ]);
  }

  function screenParLearned() {
    var data = S.parLearned;
    if (!data) return screenLoading();

    if (!data.cards.length) {
      return h('div', { class: 'page' }, [
        h('div', { class: 'empty' }, [
          h('div', { class: 'empty__title', text: 'Пока пусто' }),
          h('div', {
            class: 'empty__note',
            text: 'Сюда попадают группы, которые ты закрыл: сразу — если нажал «Знаю» '
              + 'на новой, или после двух повторов по таймеру.'
          })
        ])
      ]);
    }

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: 'Выучено ' + data.learned + ' из ' + data.total }),
      h('div', { class: 'sub', text: 'Сверху те, что закрыты последними' }),
      h('div', { class: 'word-list' }, data.cards.map(function (card) {
        return parRow(card, parRing(3, 'var(--ring-done)', '✓'), null);
      }))
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Средства выразительности, задание №22                              */
  /* ------------------------------------------------------------------ */
  /* Свой раздел целиком: свои экраны, свои запросы, своя таблица в базе.
     Это не карточки — программа проверяет ответ сама, поэтому здесь нет ни
     «Знаю / Не знаю», ни таймеров, ни слабых с выученными. Копится только
     точность по трём группам приёмов. */

  var MEANS_EMPTY = 'В базе ещё нет заданий №22 — спрашивать не из чего';

  function loadMeans() {
    return api('/api/means')
      .then(function (data) { S.means = data; render(); return data; })
      .catch(function (error) { toast(errorText(error)); });
  }

  function openMeans() {
    S.means = null;
    S.meansRun = null;
    go('means');
    loadMeans();
  }

  function startMeansRun() {
    api('/api/means/session')
      .then(function (data) {
        if (!data.questions.length) { toast(MEANS_EMPTY); return; }
        // В ответе лежит и состояние раздела — второй запрос не нужен.
        S.means = data;
        S.meansRun = {
          total: data.questions.length,
          queue: data.questions.slice(),
          at: 0,
          picked: null,    // что нажал ученик
          verdict: null,   // ответ сервера: верно ли и какой приём правильный
          busy: false,
          correct: 0,
          wrong: 0
        };
        go('meansRun');
      })
      .catch(function (error) { toast(errorText(error)); });
  }

  function currentMeans() {
    if (!S.meansRun) return null;
    return S.meansRun.queue[S.meansRun.at] || null;
  }

  /** Проверяет ответ на сервере: правильный приём не уходит на клиент заранее.
   *  Пока запрос в пути, кнопки не принимают нажатий — иначе один вопрос
   *  засчитается дважды. */
  function answerMeans(term) {
    var run = S.meansRun;
    var question = currentMeans();
    if (!run || !question || run.busy || run.verdict) return;

    run.busy = true;
    run.picked = term;
    render();

    api('/api/means/answer', {
      method: 'POST',
      body: { task_id: question.task_id, position: question.position, term: term }
    })
      .then(function (data) {
        run.busy = false;
        run.verdict = data;
        if (data.is_correct) run.correct += 1; else run.wrong += 1;
        // Точность в шапке раздела обновляем сразу: она пришла вместе с вердиктом.
        if (S.means) S.means.groups = data.groups;
        render();
      })
      .catch(function (error) {
        // Ответ не записан — возвращаем вопрос как был, ученик нажмёт заново.
        run.busy = false;
        run.picked = null;
        toast(errorText(error));
        render();
      });
  }

  function nextMeans() {
    var run = S.meansRun;
    if (!run || !run.verdict) return;
    run.at += 1;
    run.picked = null;
    run.verdict = null;
    if (run.at < run.queue.length) { render(); return; }
    go('meansDone');
    loadMeans();
  }

  function resetMeans() {
    var data = S.means || {};
    showDialog({
      title: 'Сбросить точность?',
      text: 'Будут стёрты все ' + (data.answered || 0) + ' ответов и точность по трём '
        + 'группам. Вернуть это будет нельзя.',
      actions: [
        { label: 'Отмена', kind: 'ghost' },
        {
          label: 'Стереть', kind: 'danger', onClick: function () {
            api('/api/means/reset', { method: 'POST' })
              .then(function () {
                toast('Точность сброшена');
                return loadMeans();
              })
              .catch(function (error) { toast(errorText(error)); });
          }
        }
      ]
    });
  }

  /** Три группы приёмов с точностью. Показываются только здесь: в общую
   *  статистику эти ответы не идут, она про решённые задания. */
  function meansGroups(groups) {
    return h('div', { class: 'means-groups' }, (groups || []).map(function (group) {
      return h('div', { class: 'means-group' }, [
        h('div', {
          class: 'means-group__value',
          text: group.total ? group.accuracy + '%' : '—'
        }),
        h('div', { class: 'means-group__label', text: group.title }),
        h('div', {
          class: 'means-group__note',
          text: group.total ? group.correct + ' из ' + group.total : 'нет ответов'
        })
      ]);
    }));
  }

  function screenMeans() {
    var data = S.means;
    if (!data) return screenLoading();

    return h('div', { class: 'page' }, [
      h('div', { class: 'h2', text: data.title }),
      h('div', { class: 'sub', text: data.subtitle }),

      meansGroups(data.groups),

      h('div', { class: 'stack mt-24' }, [
        h('button', {
          class: 'btn btn--primary', type: 'button', disabled: !data.available,
          onClick: startMeansRun
        }, data.available ? 'Начать подход' : 'Вопросов пока нет'),
        h('button', {
          class: 'btn btn--ghost', type: 'button', disabled: !data.answered,
          onClick: resetMeans
        }, 'Сбросить точность')
      ]),

      h('div', { class: 'banner' }, data.available
        ? 'В подходе ' + Math.min(data.size, data.available) + ' вопросов. Они случайные, '
          + 'всего их сейчас ' + data.available + '.'
        : MEANS_EMPTY)
    ]);
  }

  function meansOption(term, run) {
    var classes = ['option'];
    var mark = '';
    if (run.verdict) {
      if (term === run.verdict.correct) { classes.push('is-correct'); mark = '✓'; }
      else if (term === run.picked) { classes.push('is-wrong'); mark = '✕'; }
    } else if (term === run.picked) {
      classes.push('is-picked');
      mark = '•';
    }
    return h('button', {
      class: classes.join(' '), type: 'button',
      onClick: function () { answerMeans(term); }
    }, [
      h('div', { class: 'option__mark', text: mark }),
      h('div', { class: 'option__text', text: term })
    ]);
  }

  function screenMeansRun() {
    var run = S.meansRun;
    var question = currentMeans();
    if (!run || !question) return screenLoading();

    return h('div', { class: 'page' }, [
      h('div', { class: 'card-top' }, [
        h('div', { class: 'card-top__count', text: (run.at + 1) + ' / ' + run.total }),
        h('div', { class: 'card-top__group', text: 'задание №22' })
      ]),

      h('div', { class: 'means-quote', text: question.text }),

      h('div', { class: 'options' }, question.options.map(function (term) {
        return meansOption(term, run);
      })),

      run.verdict
        ? h('button', {
            class: 'btn btn--primary mt-24', type: 'button', onClick: nextMeans
          }, run.at + 1 < run.total ? 'Дальше' : 'Итог подхода')
        : null
    ]);
  }

  function screenMeansDone() {
    var run = S.meansRun || { total: 0, correct: 0, wrong: 0 };
    var data = S.means;
    var percent = run.total ? Math.round(run.correct * 100 / run.total) : 0;

    return h('div', { class: 'page' }, [
      h('div', { style: 'text-align:center' }, [
        h('div', { class: 'h2', text: 'Подход пройден' }),
        h('div', { class: 'sub', text: 'Верно ' + run.correct + ' из ' + run.total
          + ' · ' + percent + '%' })
      ]),

      h('div', { class: 'tiles tiles--two' }, [
        tile(run.correct, 'верно', 'tile--green'),
        tile(run.wrong, 'неверно', run.wrong ? 'tile--red' : '')
      ]),

      data ? meansGroups(data.groups) : null,

      h('div', { class: 'stack mt-24' }, [
        h('button', {
          class: 'btn btn--primary', type: 'button', onClick: startMeansRun
        }, 'Ещё подход'),
        h('button', {
          class: 'btn btn--ghost', type: 'button',
          onClick: function () { go('means'); loadMeans(); }
        }, 'К разделу')
      ]),

      h('div', { class: 'banner' },
        'Точность копится по трём группам приёмов и на выбор вопросов не влияет.')
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
        // Буква — в data-initial: саму букву рисует ::before поверх кольца,
        // иначе спектральный градиент пришлось бы класть отдельным элементом.
        h('div', { class: 'avatar', 'data-initial': initial }),
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
    cardsLearned: ['Выученные слова', 'закрытые карточки'],
    cardsWeak: ['Слабые слова', 'что ещё на повторе'],
    cardsMenu: ['Карточки', 'выбери колоду'],
    par: ['Паронимы', 'задание №5'],
    parRun: ['Паронимы', 'вспомни и проверь'],
    parDone: ['Паронимы', 'итог подхода'],
    parWeak: ['Слабые паронимы', 'что ещё на повторе'],
    parLearned: ['Выученные паронимы', 'закрытые группы'],
    means: ['Средства выразительности', 'задание №22'],
    meansRun: ['Средства выразительности', 'выбери приём'],
    meansDone: ['Средства выразительности', 'итог подхода']
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
    if (S.parRun) parts.push(S.parRun.at, S.parRun.shown, S.parRun.round);
    if (S.meansRun) parts.push(S.meansRun.at, S.meansRun.picked, !!S.meansRun.verdict);
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
    if (S.screen === 'parRun' && S.parRun) {
      title = S.parRun.round > 1
        ? ['Паронимы · добиваем', 'пока не вспомнишь']
        : ['Паронимы · ' + (S.parRun.at + 1) + '/' + S.parRun.total, 'вспомни и проверь'];
    }
    if (S.screen === 'meansRun' && S.meansRun) {
      title = ['Средства · ' + (S.meansRun.at + 1) + '/' + S.meansRun.total, 'выбери приём'];
    }
    dom.title.textContent = title[0];
    dom.sub.textContent = title[1];

    dom.back.hidden = !canGoBack();
    updateBackButton();

    // Пока идёт анкета первого входа, ходить по вкладкам некуда: тренажёр ещё
    // не открыт. При правке из профиля вкладки остаются на месте.
    dom.tabbar.hidden = S.screen === 'onboarding' && !(S.onb && S.onb.editing);

    // Активный раздел отмечает линза. Класс `is-active` на строке ставит сам
    // таббар и только он: снаружи стекла вкладка горит, лишь когда линза на
    // ней остановилась. Здесь считаем только, куда её вести.
    var activeTab = 0;
    Array.prototype.forEach.call(dom.tabs, function (tab, index) {
      if (tab.getAttribute('data-tab') === S.tab) activeTab = index;
    });
    TabBar.select(activeTab);

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
        cardsLearned: screenCardsLearned,
        cardsWeak: screenCardsWeak,
        cardsMenu: screenCardsMenu,
        par: screenPar,
        parRun: screenParRun,
        parDone: screenParDone,
        parWeak: screenParWeak,
        parLearned: screenParLearned,
        means: screenMeans,
        meansRun: screenMeansRun,
        meansDone: screenMeansDone
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
  /* Таббар: одна линза жидкого стекла                                  */
  /* ------------------------------------------------------------------ */
  /* Архитектура. Четыре настоящие кнопки живут в .tabbar__row и ничем не
     украшены. Над ними лежит ОДНА линза, которая ездит между ними: она и есть
     весь эффект. Своего стеклянного элемента нет ни у одной вкладки.

     Преломление настоящее, а не имитация: пиксели под линзой физически
     сдвигаются картой смещения через feDisplacementMap. Путей два, и
     выбираются они по движку:

       · Chromium — линза лежит НАД кнопками и гнёт их через
         `backdrop-filter: url(#id)`. Преломляется буквально то, что
         отрисовано за ней: живые кнопки, их текст и иконки.

       · WebKit (сюда попадает Telegram на iPhone) — `backdrop-filter: url()`
         там разбирается парсером, но игнорируется при отрисовке. Проверено на
         устройстве пользователя: CSS.supports отвечает «да» и врёт. Поэтому
         фильтр вешается обычным `filter: url()` на зеркало — клоны тех же
         кнопок внутри линзы. Это не скриншот и не canvas: живые узлы, текст
         остаётся текстом, а MutationObserver держит копию в точном
         соответствии с оригиналом, включая класс активного раздела.

     Геометрия всегда из живых getBoundingClientRect(): ни одной вшитой
     координаты. Движение считает пружина в одном requestAnimationFrame,
     который пишет три transform и ничего не перерисовывает. */

  var TabBar = (function () {

    var GLASS = {
      /* --- форма линзы --- */
      insetX: 10,        // боковые поля: из них получается пропорция капсулы
      insetY: 0,         // по высоте линза занимает строку целиком
      mapMax: 220,       // предел разрешения карты; в WebKit больше нельзя

      /* --- преломление --- */
      /* Настройки карты из альфы (путь WebKit, он же основной).
         reach — размытие альфы в долях высоты линзы: насколько глубоко
         преломление достаёт внутрь. gradStep — плечо, на котором берётся
         производная: чем больше, тем сильнее и мягче поле. */
      reach: 0.12,
      gradStep: 0.095,
      // Размазывание у кромки, в долях высоты линзы. Полоса, в которой оно
      // работает, задаётся тем же `reach`, поэтому матовость сама садится
      // туда, где стекло гнёт сильнее всего. Ноль выключает — это четыре
      // примитива в цепочке. Только для пути зеркала: в режиме Chromium
      // маску кромки взять неоткуда, там фильтру достаётся непрозрачный фон.
      // Размазывание кромки ВЫКЛЮЧЕНО: это второе гауссово размытие плюс
      // четыре композита, самая дорогая часть всей цепочки. Вернуть — 0.05.
      edgeBlur: 0,

      /* --- текстура кривого зеркала --- */
      // Сила ряби. Ноль полностью убирает турбулентность и четыре
      // сопутствующих примитива, возвращая прежнюю оптику один в один.
      textureStrength: 3.0,
      // Частота шума по осям. Вдоль X густо, вдоль Y редко — отсюда вытянутые
      // вертикальные валы вместо равномерных пятен.
      textureFreqX: 0.05,
      textureFreqY: 0.006,
      // Октавы: одна даёт гладкую зыбь, две добавляют неровность помельче.
      // Каждая октава стоит денег, три уже превращают стекло в грязь.
      // ОДНА октава. Вторая стоит примерно вдвое дороже на самом дорогом
      // примитиве цепочки, а на нашей узкой краевой полосе её мелкая деталь
      // всё равно не видна: снимки с одной и двумя октавами не отличаются.
      textureOctaves: 1,
      // Как быстро текстура нарастает к краю. 2 — плавно от середины,
      // 4 — почти вся деформация в последней четверти радиуса.
      // 2 — плавно нарастает от середины к краю (середина чистая, у кромки
      // сильно). 4 — почти вся деформация в последней четверти радиуса, но на
      // нашей узкой полосе это уже почти не видно.
      textureEdgePow: 2,
      // Вертикальная составляющая ряби. Держим маленькой: линза низкая, и
      // вертикальный сдвиг смазывает подпись.
      textureAxisY: 0.12,
      // Насколько туго полоса размазывания прижата к контуру. Единица —
      // широкая полоса на пол-линзы, размазывает и подпись тоже; больше —
      // теснее к краю. Ноль в профиле приходится на (k-1)/k, всё ниже
      // обрезается.
      edgeSharp: 2.6,

      /* Настройки карты картинкой (путь Chromium). Здесь профиль считается
         на canvas, потому что формы линзы фильтру взять неоткуда. */
      depth: 0.55,
      profile: 3,
      // Ширина полосы у самого контура, на которой смещение гасится в ноль.
      // Без неё поле обрывается скачком, и обрыв читается ровной дугой — тот
      // самый шов-полукруг из прошлой реализации.
      rimFade: 2,
      // Базовое преломление линзы — то самое «увеличение» по кромке. Ноль
      // выключает его вместе с четырьмя примитивами и оставляет только
      // текстуру кривого зеркала. Единица возвращает прежнее стекло.
      baseField: 0,

      // Сила смещения в долях высоты линзы. Это главная ручка. В прошлый раз
      // я убавил её по снимку из headless до 0.085 и получил на подписи сдвиг
      // в сотую пикселя — на телефоне это выглядело как «капля без оптики».
      // Теперь берём заведомо заметно: убавить по живому устройству дешевле,
      // чем второй раз гадать, работает эффект или нет.
      refract: 0.3,
      // Вертикальная составляющая. Полностью не гасим: без неё стекло плоское.
      // Но и не единица: линза широкая, под ней одна строка, и сильный
      // вертикальный сдвиг её двоит.
      // Вертикальная составляющая выключена — ради кадров, по решению
      // пользователя после замера на устройстве. Ноль включает дешёвую ветку:
      // вся вертикальная половина цепочки не строится вовсе, пять примитивов
      // заменяются одним, и в цепочке остаётся восемь вместо тринадцати.
      //
      // Потеря невелика. Линза широкая и низкая, под ней одна строка: на
      // глаз работает почти только горизонтальный выгиб, он и читается как
      // стекло. Вертикальный сдвиг вдобавок смазывал низ букв, потому и был
      // приглушён до 0.38 ещё до замеров.
      axisY: 0,
      // Насколько сильнее гнёт прижатая линза.
      pressRefract: 0.5,
      // Расслоение цвета: разбег силы смещения между каналами в долях.
      // Ноль полностью выключает трёхпроходную схему.
      //
      // ВЫКЛЮЧЕНО ИЗ-ЗА ЦЕНЫ. Линзу нельзя вынести в отдельный слой
      // композитора — вместе со слоем WebKit выбрасывает и сам фильтр, — а
      // значит вся цепочка пересчитывается на процессоре каждый кадр.
      // Расслоение стоит трёх проходов смещения вместо одного и плюс пять
      // примитивов на сборку: это больше трети всей работы кадра, и таббар
      // от неё заметно терял кадры на телефоне. Эффект от неё — цветная кайма
      // толщиной меньше пикселя. Плохой размен, но включается одним числом.
      aberration: 0,

      /* --- пружины --- */
      // Позиция: чуть недодемпфирована, отсюда лёгкий перелёт и мягкий доводчик.
      posK: 210, posC: 22,
      // Деформация: демпфирована заметно слабее — отсюда wobble после остановки.
      formK: 150, formC: 11,
      // Нажатие: почти без перелёта, оно не должно «звенеть».
      pressK: 260, pressC: 26,
      // Скорость -> растяжение. Скорость меряется во вкладках в секунду.
      stretchGain: 0.034,
      stretchMax: 0.32,
      // Встречное сжатие по вертикали: объём стекла сохраняется.
      squash: 0.55,
      // Надувание при удержании.
      // Надувание при удержании. Линза растёт коробкой, а содержимое под ней
      // остаётся на месте — обратная трансформация на зеркале это и делает.
      // Поэтому при росте под стекло заезжает больше соседнего содержимого,
      // как под настоящей лупой, а не растягивается то, что уже было.
      pressLens: 0.35,
      // Подача плашки выключена: любая трансформация предка — лишний повод
      // для WebKit вынести поддерево в композитор и потерять фильтр. Ради
      // едва заметного движения рисковать всем эффектом не стоит.
      pressPlate: 0,

      dragThreshold: 6
    };

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var XLINK_NS = 'http://www.w3.org/1999/xlink';

    var el = null;      // ссылки на узлы
    var geo = null;     // измеренная геометрия
    var fx = null;      // { defs, filter, id, disp[], w, h, version }
    var path = 'mirror';
    var tabs = [];
    var onSelect = null;
    var reduced = false;

    // Состояние пружин. Это всё, что меняется в кадре.
    var sim = {
      pos: 0, vel: 0, target: 0,
      form: 0, formVel: 0,
      press: 0, pressVel: 0, pressTarget: 0
    };
    var frame = 0;
    var lastTime = 0;
    var booted = false;  // первый select() ставит линзу на место без проезда
    var dragging = false;
    var dragPrev = 0;    // положение линзы в прошлом кадре ведения
    var dispScale = 0;   // последнее записанное значение, чтобы не дёргать SVG зря
    var lastPlateT = '';

    /* --- мелкая математика ------------------------------------------- */

    function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

    function smoothStep(edge0, edge1, v) {
      var t = clamp((v - edge0) / (edge1 - edge0), 0, 1);
      return t * t * (3 - 2 * t);
    }

    /** Знаковое расстояние до края скруглённого прямоугольника: внутри < 0. */
    function shapeSDF(px, py, halfW, halfH, radius) {
      var ex = Math.abs(px) - halfW + radius;
      var ey = Math.abs(py) - halfH + radius;
      var ox = ex > 0 ? ex : 0;
      var oy = ey > 0 ? ey : 0;
      var corner = (ox > 0 || oy > 0) ? Math.sqrt(ox * ox + oy * oy) : 0;
      return corner + Math.min(Math.max(ex, ey), 0) - radius;
    }

    function svgNode(name, attrs) {
      var node = document.createElementNS(SVG_NS, name);
      for (var key in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, key)) {
          node.setAttribute(key, String(attrs[key]));
        }
      }
      return node;
    }

    /* --- карта смещения ----------------------------------------------- */
    /* Для каждой точки линзы считается расстояние до её края, из него —
       вектор, куда сдвинуть пиксель. Сдвиг по X пишется в красный канал, по
       Y — в зелёный; синий не используется, блик рисует CSS.

       Вектор смотрит ВНУТРЬ, к середине линзы. Это и делает стекло выпуклым:
       точка у кромки берёт пиксель из глубины, значит середина картинки
       растягивается на всю линзу — увеличение. Развернуть вектор наружу —
       получится вогнутое стекло, оно сжимает.

       Величина в середине ровно ноль и растёт к кромке, а в последних двух
       пикселях гасится обратно в ноль. Оба нуля обязательны: без первого
       середина линзы плывёт, без второго поле обрывается скачком и обрыв
       виден ровной дугой по контуру. */
    function buildMap(w, h) {
      var mw = Math.max(48, Math.min(GLASS.mapMax, Math.round(w)));
      var mh = Math.max(24, Math.round(mw * h / w));
      var canvas = document.createElement('canvas');
      canvas.width = mw;
      canvas.height = mh;
      var ctx = canvas.getContext && canvas.getContext('2d');
      if (!ctx || !window.ImageData) return null;

      var halfW = w / 2;
      var halfH = h / 2;
      var radius = Math.min(halfW, halfH);
      var depth = Math.max(1, GLASS.depth * Math.min(halfW, halfH));
      var stepX = w / mw;
      var stepY = h / mh;
      var eps = 0.35;

      var vx = new Float32Array(mw * mh);
      var vy = new Float32Array(mw * mh);
      var peak = 0;
      var r, c, i;

      for (r = 0; r < mh; r++) {
        var py = (r + 0.5) * stepY - halfH;
        for (c = 0; c < mw; c++) {
          var px = (c + 0.5) * stepX - halfW;
          var sdf = shapeSDF(px, py, halfW, halfH, radius);
          if (sdf >= 0) continue;                 // снаружи линзы сдвига нет

          var deep = Math.min(1, -sdf / depth);   // 0 у кромки, 1 в глубине
          var mag = Math.pow(1 - deep, GLASS.profile);
          mag *= smoothStep(0, GLASS.rimFade, -sdf);
          if (mag <= 0) continue;

          // Нормаль к контуру — численно, по градиенту того же расстояния.
          var gx = shapeSDF(px + eps, py, halfW, halfH, radius)
                 - shapeSDF(px - eps, py, halfW, halfH, radius);
          var gy = shapeSDF(px, py + eps, halfW, halfH, radius)
                 - shapeSDF(px, py - eps, halfW, halfH, radius);
          var len = Math.sqrt(gx * gx + gy * gy);
          if (len < 1e-6) continue;

          i = r * mw + c;
          // Минус: градиент смотрит наружу, а тянуть надо внутрь.
          var dx = -(gx / len) * mag;
          var dy = -(gy / len) * mag * GLASS.axisY;
          vx[i] = dx;
          vy[i] = dy;
          if (Math.abs(dx) > peak) peak = Math.abs(dx);
          if (Math.abs(dy) > peak) peak = Math.abs(dy);
        }
      }

      // Нормируем по общему максимуму, а не обрезаем по каналу: срезанные
      // значения дают кольцо одинакового сдвига и ступеньку за ним.
      var data = new Uint8ClampedArray(mw * mh * 4);
      var norm = peak > 0 ? 0.5 / peak : 0;
      for (i = 0; i < mw * mh; i++) {
        var at = i * 4;
        data[at] = ((0.5 + vx[i] * norm) * 255 + 0.5) | 0;
        data[at + 1] = ((0.5 + vy[i] * norm) * 255 + 0.5) | 0;
        data[at + 2] = 128;
        data[at + 3] = 255;
      }
      ctx.putImageData(new ImageData(data, mw, mh), 0, 0);
      return canvas.toDataURL();
    }

    /* --- карта смещения из собственной альфы --------------------------- */
    /* Способ, который реально работает в WebKit. Проверен на устройстве
       пользователя: `feImage` с data-URL там мёртв — картинка не доезжает до
       фильтра, и линза гнёт пустоту, — а вот сам `feDisplacementMap` на
       обычном HTML живой.

       Поэтому карта не рисуется вовсе, а выводится из формы самой линзы.
       Размываем её альфу: получается плавная ступенька, единица внутри, ноль
       снаружи, у кромки переход. Производная этой ступеньки и есть поле
       смещения — ноль в глубине, где ступенька ровная, наибольшее у кромки,
       где она растёт, и снова ноль снаружи. Ровно тот профиль, который
       раньше считался на canvas, только даром и без единой картинки.

       Направление получается внутрь само собой: у левой кромки ступенька
       растёт вправо, значит красный канал больше половины, значит пиксель
       берётся правее — из глубины. Это и есть выпуклое стекло, оно увеличивает.

       Побочная выгода крупная: карта подстраивается под любой размер линзы
       сама. Вместе с картинкой уходит и ловушка WebKit «не перестраивай карту
       на ходу» — перестраивать больше нечего.

       Обязательное условие: у элемента с фильтром должна быть форма линзы,
       то есть обрезка по скруглению. Из неё берётся альфа, а из альфы — всё
       остальное. Обрезка ДО фильтра здесь безопасна, потому что смещение
       смотрит внутрь и наружных пикселей ему не нужно. */
    function buildAlphaFilter(id, h, scale) {
      // Размытие альфы и плечо производной — в пикселях, от высоты линзы.
      var blur = GLASS.reach * h;
      var step = GLASS.gradStep * h;
      var filter = svgNode('filter', {
        id: id,
        primitiveUnits: 'userSpaceOnUse',
        // sRGB, а не linearRGB: перевод в линейное пространство и обратно —
        // лишняя работа на каждом примитиве, а на карте смещения он не нужен.
        'color-interpolation-filters': 'sRGB',
        // Область держим тесной. Каждый лишний процент — это пиксели, через
        // которые прогоняется вся цепочка, причём каждый кадр. Широкий запас
        // здесь не нужен: смещение смотрит ВНУТРЬ, наружные пиксели ему не
        // нужны, а размытию кромки хватает пары своих сигм за контуром.
        // Считано под линзу 72x45: остаётся по 6.5 px с боков и по 8 сверху
        // и снизу, при ходе смещения 4.3 px и сигме размытия 2.2 px.
        x: '-9%', y: '-18%', width: '118%', height: '136%'
      });

      filter.appendChild(svgNode('feGaussianBlur', {
        'in': 'SourceAlpha', stdDeviation: blur, result: 'blur'
      }));
      // Альфа переезжает в цвет, сама альфа становится единицей: дальше можно
      // считать разности, не думая о предумножении.
      //
      // Зелёный канал при этом ОБНУЛЯЕТСЯ, и это не мелочь. Через два шага
      // один из сдвигов инвертируется, у него зелёный станет единицей, а в
      // полусреднем выйдет ровно 0.5 — то самое «вертикального смещения нет».
      // Значит результат разности можно скармливать смещению напрямую, и
      // отдельный примитив, который раньше прибивал зелёный к середине,
      // больше не нужен. Минус один проход по всей области в каждом кадре.
      if (!GLASS.baseField) {
        /* Базовое преломление выключено: строится только текстура кривого
           зеркала, и она сама становится картой. Уходит четыре примитива —
           две матрицы и два сдвига, — а вместе со сборкой карты пять.
           Это не оптимизация ради оптимизации: цепочка считается на процессоре
           каждый кадр, и на телефоне цена измеряется прямо в кадрах. */
        return finish(filter, scale, h);
      }

      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'blur', type: 'matrix', result: 'field',
        values: '0 0 0 1 0  0 0 0 0 0  0 0 0 1 0  0 0 0 0 1'
      }));

      // Производная как разность двух сдвигов. Складывать их через feComposite
      // arithmetic со смещением k4 нельзя: k4 прибавляется и к альфе, та
      // становится 0.5, следующий примитив делит цвет на неё обратно, и всё
      // вылетает в единицу. Поэтому один сдвиг инвертируется цветовой
      // матрицей, а дальше берётся честное полусреднее: 0.5*cR + 0.5*(1-cL)
      // даёт то же 0.5 + 0.5*(cR-cL), но альфа честно остаётся единицей.
      var INV = '-1 0 0 0 1  0 -1 0 0 1  0 0 -1 0 1  0 0 0 0 1';

      filter.appendChild(svgNode('feOffset', { 'in': 'field', dx: -step, dy: 0, result: 'xR' }));
      filter.appendChild(svgNode('feOffset', { 'in': 'field', dx: step, dy: 0, result: 'xLraw' }));
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'xLraw', type: 'matrix', values: INV, result: 'xL'
      }));
      var gx = svgNode('feComposite', {
        'in': 'xR', in2: 'xL', operator: 'arithmetic',
        k1: 0, k2: 0.5, k3: 0.5, k4: 0, result: 'gx'
      });
      filter.appendChild(gx);

      var ay = GLASS.axisY;

      if (ay <= 0) {
        // Вертикали нет — её ветка не строится вовсе, а `gx` уже является
        // готовой картой: в красном канале сдвиг по X, в зелёном ровно 0.5,
        // то есть по Y не двигаем. Ни одного лишнего примитива.
        gx.setAttribute('result', 'map');
        return finish(filter, scale, h);
      }

      filter.appendChild(svgNode('feOffset', { 'in': 'field', dx: 0, dy: -step, result: 'yD' }));
      filter.appendChild(svgNode('feOffset', { 'in': 'field', dx: 0, dy: step, result: 'yUraw' }));
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'yUraw', type: 'matrix', values: INV, result: 'yU'
      }));
      filter.appendChild(svgNode('feComposite', {
        'in': 'yD', in2: 'yU', operator: 'arithmetic',
        k1: 0, k2: 0.5, k3: 0.5, k4: 0, result: 'gy'
      }));

      // Сборка карты: сдвиг по X в красный канал, по Y в зелёный. Альфа у
      // обоих слагаемых принудительно единица, поэтому сумма её не ломает.
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'gx', type: 'matrix', result: 'mR',
        values: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0 1'
      }));
      // Вертикальная составляющая гасится здесь же, множителем в матрице:
      // G = axisY*G + 0.5*(1-axisY), то есть значение поджимается к середине,
      // где сдвиг нулевой. Отдельным примитивом это делать нельзя — любое
      // слагаемое через k4 портит альфу. Гасить нужно: линза широкая, под ней
      // одна строка, и сильный вертикальный сдвиг просто смазывает подпись.
      // Вертикальная производная лежит в КРАСНОМ канале gy — зелёный там
      // теперь постоянный, его обнулили ещё в `field`. Поэтому матрица
      // переносит красный в зелёный, попутно гася силу множителем ay.
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'gy', type: 'matrix', result: 'mG',
        values: '0 0 0 0 0  ' + ay + ' 0 0 0 ' + (0.5 * (1 - ay))
          + '  0 0 0 0 0  0 0 0 0 1'
      }));
      filter.appendChild(svgNode('feComposite', {
        'in': 'mR', in2: 'mG', operator: 'arithmetic',
        k1: 0, k2: 1, k3: 1, k4: 0, result: 'map'
      }));

      return finish(filter, scale, h);
    }

    /* --- накладная текстура кривого зеркала ------------------------------
       Кривое зеркало — это НЕРОВНАЯ поверхность, а не увеличение. Ровное поле,
       каким бы сильным оно ни было, даёт только «крупнее» или «мутнее»; чтобы
       содержимое плыло и меняло форму, поле должно гулять само по себе.

       Неровность берёт feTurbulence: он порождает шум Перлина прямо внутри
       фильтра. Картинку сюда подать нельзя ни в каком виде — feImage в этом
       WebView мёртв, проверено на устройстве, — и это не обход, а штатное
       применение примитива: волнистое стекло и вода в спецификации SVG
       сделаны именно им.

       Шум идёт НЕ поверх картинки, а В КАРТУ СМЕЩЕНИЯ: он прибавляется к
       готовой карте, и дальше feDisplacementMap двигает по ней живой DOM.
       То есть рябь физически искажает содержимое, а не рисуется сверху.

       Частота по осям разная: вдоль X густо, вдоль Y редко. Получаются
       вытянутые вертикальные валы — так и выглядит кривое зеркало в комнате
       смеха, и это хорошо ложится на наше горизонтальное смещение.

       Краевая маска отдельная и своя. У полосы размазывания горб сидит НА
       контуре и гаснет в обе стороны, а здесь нужно другое: ноль в середине
       линзы и рост к краю. Берём (2*(1-a))^2 от размытой альфы — в середине
       альфа единица и маска ноль, на контуре альфа половина и маска единица.
       Ещё одно возведение в квадрат сгоняет почти всю деформацию в последнюю
       четверть радиуса.

       Цена честная: feTurbulence самый дорогой примитив из всех здешних, и
       считается он каждый кадр — линзу нельзя вынести в отдельный слой.
       textureStrength = 0 убирает его вместе со всеми сопутствующими
       примитивами и возвращает ровно прежнюю оптику. */
    function addTexture(filter) {
      var amp = GLASS.textureStrength;
      if (amp <= 0) return 'map';

      /* Краевая маска: ноль в середине линзы, единица на контуре.
         (2*(1-a))^2 = 4a^2 - 8a + 4 — ровно один arithmetic по альфе.

         А дальше обязательный шаг, на котором я уже споткнулся: значение надо
         перенести из АЛЬФЫ В ЦВЕТ. feComposite перемножает по цветовым
         каналам, а у результата первого шага цвет всюду единица (SourceAlpha
         приходит с нулевым цветом, и k4 задирает его в потолок). Если подать
         такую маску как есть, множитель всюду равен единице: текстура ляжет
         равномерно по всей линзе, а показатель края не будет значить ничего. */
      filter.appendChild(svgNode('feComposite', {
        'in': 'blur', in2: 'blur', operator: 'arithmetic',
        k1: 4, k2: -4, k3: -4, k4: 4, result: 'edgeA'
      }));
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'edgeA', type: 'matrix', result: 'edge0',
        values: '0 0 0 1 0  0 0 0 1 0  0 0 0 1 0  0 0 0 0 1'
      }));
      var edge = 'edge0';
      if (GLASS.textureEdgePow >= 4) {
        // Ещё квадрат: почти вся деформация уходит в последнюю четверть радиуса.
        filter.appendChild(svgNode('feComposite', {
          'in': 'edge0', in2: 'edge0', operator: 'arithmetic',
          k1: 1, k2: 0, k3: 0, k4: 0, result: 'edge1'
        }));
        edge = 'edge1';
      }

      filter.appendChild(svgNode('feTurbulence', {
        type: 'fractalNoise',
        baseFrequency: GLASS.textureFreqX + ' ' + GLASS.textureFreqY,
        numOctaves: Math.max(1, GLASS.textureOctaves | 0),
        seed: 5, stitchTiles: 'noStitch', result: 'noiseRaw'
      }));

      // Сила и распределение по осям. Горизонталь основная, вертикаль почти
      // погашена: линза низкая, и вертикальный сдвиг просто смазывает подпись.
      // Альфа принудительно единица: дальше всё считается на предумноженных
      // значениях, и неполная альфа их испортила бы.
      var ay = GLASS.textureAxisY;
      filter.appendChild(svgNode('feColorMatrix', {
        'in': 'noiseRaw', type: 'matrix', result: 'noise',
        values: amp + ' 0 0 0 ' + (0.5 * (1 - amp))
          + '  0 ' + (amp * ay) + ' 0 0 ' + (0.5 * (1 - amp * ay))
          + '  0 0 0 0 0  0 0 0 0 1'
      }));

      // Прижимаем шум маской: tex = 0.5 + edge*(noise - 0.5).
      // Альфа выходит единицей сама: 1 - 0.5 + 0.5.
      filter.appendChild(svgNode('feComposite', {
        'in': 'noise', in2: edge, operator: 'arithmetic',
        k1: 1, k2: 0, k3: -0.5, k4: 0.5, result: 'tex'
      }));

      // Если базового преломления нет, текстура и есть карта — складывать
      // не с чем, и лишний проход по всей области не нужен.
      if (!GLASS.baseField) return 'tex';

      // Иначе прибавляем к готовой карте: map + tex - 0.5.
      filter.appendChild(svgNode('feComposite', {
        'in': 'map', in2: 'tex', operator: 'arithmetic',
        k1: 0, k2: 1, k3: 1, k4: -0.5, result: 'mapTex'
      }));
      return 'mapTex';
    }

    /** Хвост цепочки: смещение, а за ним, если включено, размазывание кромки. */
    function finish(filter, scale, h) {
      // Текстура подмешивается В КАРТУ, до смещения: она должна искажать DOM,
      // а не лежать поверх него.
      var mapName = addTexture(filter);
      var radius = GLASS.edgeBlur * h;
      if (radius <= 0) {
        return { node: filter, disp: addDisplacement(filter, scale, null, mapName) };
      }
      var disp = addDisplacement(filter, scale, 'disp', mapName);
      addEdgeBlur(filter, radius);
      return { node: filter, disp: disp };
    }

    /* --- цепочка смещения и расслоение цвета --------------------------- */
    /* Общая часть обоих путей: берёт готовый результат `map` и гонит через
       него исходник. Расслоение цвета — три прохода с чуть разной силой, и
       вся сложность в альфе. Прошлая попытка складывала проходы через
       feComposite arithmetic, не тронув альфу: она набегала до трёх,
       обрезалась в единицу, а цвет, поделённый обратно на меньшую альфу,
       уходил в белое — линза выбеливалась.

       Здесь каждый проход сначала идёт через feColorMatrix, который оставляет
       ровно один канал и ПРИНУДИТЕЛЬНО ставит альфу в единицу (последняя
       строка матрицы `0 0 0 0 1`). Тогда домножение на альфу — пустая
       операция, цвет при сложении не пересчитывается, а суммарная альфа
       упирается в ту же единицу. Обнулять альфу нельзя: композиция идёт в
       предумноженном виде, и вместе с альфой обнулился бы цвет. */
    function addDisplacement(filter, scale, out, mapName) {
      var MAP = mapName || 'map';
      var ab = GLASS.aberration;
      var disp = [];

      if (ab <= 0) {
        var one = svgNode('feDisplacementMap', {
          'in': 'SourceGraphic', in2: MAP, scale: scale,
          xChannelSelector: 'R', yChannelSelector: 'G'
        });
        if (out) one.setAttribute('result', out);
        filter.appendChild(one);
        disp.push({ node: one, k: 1 });
        return disp;
      }

      var channels = [
        { key: 'R', k: 1 - ab, m: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0 1' },
        { key: 'G', k: 1,      m: '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 0 1' },
        { key: 'B', k: 1 + ab, m: '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 0 1' }
      ];
      var prev = null;
      for (var n = 0; n < channels.length; n++) {
        var ch = channels[n];
        var node = svgNode('feDisplacementMap', {
          'in': 'SourceGraphic', in2: MAP, scale: scale * ch.k,
          xChannelSelector: 'R', yChannelSelector: 'G', result: 'd' + ch.key
        });
        filter.appendChild(node);
        disp.push({ node: node, k: ch.k });

        filter.appendChild(svgNode('feColorMatrix', {
          'in': 'd' + ch.key, type: 'matrix', values: ch.m, result: 'c' + ch.key
        }));

        if (prev === null) {
          prev = 'c' + ch.key;
        } else {
          var last = n === channels.length - 1;
          var add = {
            'in': 'c' + ch.key, in2: prev, operator: 'arithmetic',
            k1: 0, k2: 1, k3: 1, k4: 0
          };
          if (!last) add.result = 'sum' + n;
          else if (out) add.result = out;
          filter.appendChild(svgNode('feComposite', add));
          prev = last ? null : 'sum' + n;
        }
      }
      return disp;
    }

    /* --- размазывание у кромки ------------------------------------------ */
    /* Полоса матового стекла по краю линзы: в середине картинка резкая, к
       контуру расплывается. Именно так ведёт себя толстое стекло — у края луч
       идёт под скользящим углом и собирает свет с большего пятна.

       Маска этой полосы достаётся даром из уже посчитанного размытия альфы.
       Пусть a — размытая альфа: единица глубоко внутри, ноль снаружи, ровно
       половина на самом контуре. Тогда 4a(1-a) — горб, который равен единице
       точно на контуре и гаснет в обе стороны. Это и есть полоса кромки, и
       считается она ОДНИМ примитивом: feComposite arithmetic умеет перемножать
       вход сам на себя, а -4a² + 4a как раз такая форма.

       Ширина полосы задаётся тем же `reach`, что и глубина преломления,
       поэтому размазывание само садится туда, где стекло гнёт сильнее всего.
       Наружная половина полосы уходит под обрезку линзы и не видна. */
    function addEdgeBlur(filter, radius) {
      filter.appendChild(svgNode('feComposite', {
        'in': 'blur', in2: 'blur', operator: 'arithmetic',
        k1: -4, k2: 4, k3: 0, k4: 0, result: 'rim0'
      }));

      // Горб 4a(1-a) сам по себе слишком широк: линза всего 45 px высотой, и
      // подпись стоит внутри полосы — размазывало её целиком, а не край.
      // Полосу нужно поджать к контуру.
      //
      // Раньше это делалось двумя возведениями в квадрат, то есть двумя
      // примитивами. Одного мало (полоса остаётся широкой), а каждый проход
      // по всей области стоит заметно — по замеру именно число проходов, а не
      // сложность каждого, и роняет кадры.
      //
      // Тот же результат даёт ОДИН примитив: k*r^2 - (k-1)*r. В единице он
      // равен единице, а ноль у него в (k-1)/k — то есть всё ниже этого порога
      // обрезается, и полоса выходит даже теснее, чем от двух возведений.
      // Альфа при этом честно остаётся единицей: k - (k-1) = 1.
      var k = GLASS.edgeSharp;
      filter.appendChild(svgNode('feComposite', {
        'in': 'rim0', in2: 'rim0', operator: 'arithmetic',
        k1: k, k2: -(k - 1) / 2, k3: -(k - 1) / 2, k4: 0, result: 'rim'
      }));
      var mask = 'rim';

      filter.appendChild(svgNode('feGaussianBlur', {
        'in': 'disp', stdDeviation: radius, result: 'soft'
      }));
      // Мягкую копию оставляем только в полосе кромки и кладём поверх резкой.
      filter.appendChild(svgNode('feComposite', {
        'in': 'soft', in2: mask, operator: 'in', result: 'softRim'
      }));
      filter.appendChild(svgNode('feComposite', {
        'in': 'softRim', in2: 'disp', operator: 'over'
      }));
    }


    /* --- карта картинкой: только для Chromium --------------------------- */
    /* В режиме backdrop-filter трюк с альфой не работает: фильтру достаётся
       фон, а он непрозрачный целиком, и производная его альфы всюду ноль.
       Формы линзы там взять неоткуда, поэтому карта остаётся нарисованной на
       canvas и подаётся через feImage. В Chromium он живой — проверено. */
    function buildImageFilter(id, href, w, h, scale) {
      var filter = svgNode('filter', {
        id: id,
        // Область шире самой линзы: feDisplacementMap берёт пиксели только
        // изнутри области, и когда она совпадает с линзой, у кромки тянуть
        // неоткуда — вместо соседнего текста подставляется прозрачность.
        filterUnits: 'objectBoundingBox',
        primitiveUnits: 'userSpaceOnUse',
        'color-interpolation-filters': 'sRGB',
        x: -0.3, y: -0.3, width: 1.6, height: 1.6
      });

      filter.appendChild(svgNode('feFlood', {
        'flood-color': 'rgb(128,128,128)', 'flood-opacity': 1, result: 'mapBg'
      }));
      var image = svgNode('feImage', {
        preserveAspectRatio: 'none', result: 'rawMap',
        x: 0, y: 0, width: w, height: h
      });
      image.setAttribute('href', href);
      image.setAttributeNS(XLINK_NS, 'xlink:href', href);
      filter.appendChild(image);
      filter.appendChild(svgNode('feComposite', {
        'in': 'rawMap', in2: 'mapBg', operator: 'over', result: 'map'
      }));

      return { node: filter, disp: addDisplacement(filter, scale) };
    }

    /** Базовая сила смещения для линзы высотой h, в пикселях. */
    function baseScale(h) { return GLASS.refract * h; }

    /** Пересобирает фильтр под размер линзы. В режиме зеркала это нужно
     *  только чтобы пересчитать размытие и шаг в пикселях: сама карта
     *  подстраивается под форму сама и перестроения не требует. */
    function rebuild(w, h) {
      var iw = Math.round(w);
      var ih = Math.round(h);
      if (iw < 8 || ih < 8) return;
      if (fx.w === iw && fx.h === ih) return;

      dispScale = baseScale(ih);
      // Новый id при каждой пересборке: WebKit кэширует результат фильтра по
      // id, и без смены линза замерзает на прежнем.
      fx.version += 1;
      var id = 'tabbar-lens-' + fx.version;
      var built;

      if (path === 'backdrop') {
        var href = buildMap(iw, ih);
        if (!href) return;
        built = buildImageFilter(id, href, iw, ih, dispScale);
      } else {
        built = buildAlphaFilter(id, ih, dispScale);
      }

      if (fx.filter) fx.defs.removeChild(fx.filter);
      fx.defs.appendChild(built.node);
      fx.filter = built.node;
      fx.disp = built.disp;
      fx.id = id;
      fx.w = iw;
      fx.h = ih;

      var ref = 'url(#' + id + ')';
      if (path === 'backdrop') {
        el.lens.style.webkitBackdropFilter = ref;
        el.lens.style.backdropFilter = ref;
      } else {
        el.fx.style.filter = ref;
      }
    }

    /** Сила преломления растёт, пока линзу держат. Одно число на примитив, и
     *  только когда оно ощутимо изменилось: SVG в кадре трогать дорого. */
    function applyRefraction() {
      if (!fx || !fx.disp.length || !fx.h) return;
      var want = baseScale(fx.h) * (1 + sim.press * GLASS.pressRefract);
      if (Math.abs(want - dispScale) < 0.3) return;
      dispScale = want;
      for (var i = 0; i < fx.disp.length; i++) {
        fx.disp[i].node.setAttribute('scale', String(want * fx.disp[i].k));
      }
    }

    /* --- зеркало живого DOM -------------------------------------------- */
    /* Копия строки разделов, которую гнёт фильтр. Всё держится на том, что она
       неотличима от оригинала, поэтому синхронизация двухуровневая.

       Смена атрибута (а это почти всегда class активного раздела) переносится
       на тот же узел копии точечно. Так у копии срабатывает её собственный
       переход цвета — тот же самый и в тот же момент, что у оригинала.
       Пересоздание копии здесь не годится: свежевставленный узел не
       анимируется, он сразу рисуется конечным цветом, и все 260 мс перехода
       копия шла бы впереди оригинала. У кромки линзы это видно.

       Перестройка структуры (появился узел, изменился текст) встречает полное
       переклеивание: тогда важна не плавность, а точность. */
    var mirrorPending = false;
    var realNodes = null;
    var copyNodes = null;

    function syncMirror() {
      if (path !== 'mirror' || !el) return;
      var copy = el.row.cloneNode(true);
      var live = copy.querySelectorAll('[data-tab]');
      for (var i = 0; i < live.length; i++) {
        // Копия не должна ловить фокус, клики и попадать в выборки по data-tab.
        live[i].removeAttribute('data-tab');
        live[i].setAttribute('tabindex', '-1');
        live[i].setAttribute('aria-hidden', 'true');
      }
      // Под стеклом активны ВСЕ вкладки. Линза и есть подсветка: снаружи
      // цвет обычный, внутри активный, а граница проходит ровно по её кромке.
      // Поэтому цвет меняется в тот самый миг, когда край стекла коснулся
      // вкладки, — без порогов и без «переключилось на середине».
      var all = copy.querySelectorAll('.tab');
      for (var n = 0; n < all.length; n++) all[n].classList.add('is-active');

      el.mirror.innerHTML = '';
      el.mirror.appendChild(copy);
      // Оба дерева одинаковы по построению, поэтому узлы сопоставляются по
      // порядку обхода. Любая правка структуры проходит здесь же и списки
      // пересобирает, так что разъехаться они не могут.
      realNodes = [el.row].concat(Array.prototype.slice.call(el.row.querySelectorAll('*')));
      copyNodes = [copy].concat(Array.prototype.slice.call(copy.querySelectorAll('*')));
    }

    /** Переносит один атрибут на узел-близнец. false — не смогли, нужна
     *  полная пересборка копии. */
    function mirrorAttribute(target, name) {
      if (!realNodes || !name) return false;
      var i = realNodes.indexOf(target);
      if (i < 0 || !copyNodes[i]) return false;
      // data-tab в копии убран намеренно, обратно возвращать нельзя.
      if (name === 'data-tab') return true;
      var value = target.getAttribute(name);
      if (value === null) { copyNodes[i].removeAttribute(name); return true; }
      // Смену класса переносим, но активность у копии не отнимаем: под
      // стеклом вкладки активны всегда, это и есть подсветка линзой.
      if (name === 'class' && /(^|\s)tab(\s|$)/.test(value)
        && value.indexOf('is-active') < 0) {
        value += ' is-active';
      }
      copyNodes[i].setAttribute(name, value);
      return true;
    }

    function watchMirror() {
      if (path !== 'mirror' || !window.MutationObserver) return;
      var observer = new MutationObserver(function (records) {
        var full = false;
        for (var i = 0; i < records.length; i++) {
          var rec = records[i];
          if (rec.type !== 'attributes' || !mirrorAttribute(rec.target, rec.attributeName)) {
            full = true;
          }
        }
        if (!full || mirrorPending) return;
        mirrorPending = true;
        requestAnimationFrame(function () {
          mirrorPending = false;
          syncMirror();
        });
      });
      observer.observe(el.row, {
        subtree: true, childList: true, attributes: true, characterData: true
      });
    }

    /* --- измерения ------------------------------------------------------ */
    /* Всё до единого числа берётся из живых прямоугольников. Шаг меряется по
       расстоянию между первой и последней кнопкой, а не делением ширины
       плашки: у плашки есть поля и рамка, и расчёт по внешней ширине копит
       сдвиг к правому краю. */
    function measure() {
      if (!el || !tabs.length) return false;
      var plate = el.plate.getBoundingClientRect();
      var first = tabs[0].getBoundingClientRect();
      if (!plate.width || !first.width) return false;
      var rowBox = el.row.getBoundingClientRect();

      var step = tabs.length > 1
        ? (tabs[tabs.length - 1].getBoundingClientRect().left - first.left) / (tabs.length - 1)
        : first.width;

      // getBoundingClientRect отдаёт border box, а left/top у абсолютного
      // потомка отсчитываются от PADDING box — то есть от внутреннего края
      // рамки. Без поправки линза и зеркало съезжают ровно на толщину рамки:
      // мало, но копия перестаёт совпадать с оригиналом, и это видно.
      var plateStyle = getComputedStyle(el.plate);
      var padLeft = plate.left + (parseFloat(plateStyle.borderLeftWidth) || 0);
      var padTop = plate.top + (parseFloat(plateStyle.borderTopWidth) || 0);

      geo = {
        step: step,
        // Толщина рамки слева: по ней жест пересчитывает палец в позицию линзы.
        border: padLeft - plate.left,
        w: Math.max(8, first.width - GLASS.insetX * 2),
        h: Math.max(8, first.height - GLASS.insetY * 2),
        left: first.left - padLeft + GLASS.insetX,
        top: first.top - padTop + GLASS.insetY,
        rowW: rowBox.width,
        rowH: rowBox.height
      };
      // Сдвиг зеркала внутри линзы: столько, чтобы копия легла ровно на
      // оригинал. Считается от кнопки, а не от плашки, — тогда в разность не
      // входит ни рамка, ни поля. Ход линзы вычитается в кадре.
      geo.mx = (rowBox.left - first.left) - GLASS.insetX;
      geo.my = (rowBox.top - first.top) - GLASS.insetY;

      // Запас вокруг зеркала. Надутая линза выше строки, и без запаса её
      // верхняя и нижняя полосы оказывались пустыми: под стеклом ничего нет,
      // и сквозь него виден край плашки. Запас делается рамкой того же цвета,
      // что заливка, — тогда содержимое само отъезжает внутрь на её толщину,
      // и городить лишний узел не нужно.
      geo.pad = Math.ceil(Math.max(geo.w, geo.h) * GLASS.pressLens * 0.5) + 4;

      el.lens.style.left = geo.left + 'px';
      el.lens.style.top = geo.top + 'px';
      el.lens.style.width = geo.w + 'px';
      el.lens.style.height = geo.h + 'px';
      el.lens.style.borderRadius = (geo.h / 2) + 'px';
      el.mirror.style.width = geo.rowW + 'px';
      el.mirror.style.height = geo.rowH + 'px';
      el.mirror.style.borderWidth = geo.pad + 'px';

      rebuild(geo.w, geo.h);
      el.tabbar.classList.add('is-ready');
      return true;
    }

    /* --- кадр ----------------------------------------------------------- */

    function paint() {
      if (!geo) return;
      var tx = sim.pos * geo.step;
      // Растяжение вдоль хода и встречное сжатие поперёк: объём стекла
      // сохраняется, поэтому на разгоне линза не раздувается.
      var sx = clamp(1 + sim.form + sim.press * GLASS.pressLens, 0.5, 2);
      var sy = clamp(1 - sim.form * GLASS.squash + sim.press * GLASS.pressLens, 0.5, 2);

      // Только двумерные трансформации: translate3d вынес бы линзу в
      // отдельный слой композитора, а WebKit внутри такого слоя выбрасывает
      // SVG-фильтр вместе со всем преломлением.
      el.lens.style.transform = 'translate(' + tx.toFixed(2) + 'px,0) scale('
        + sx.toFixed(4) + ',' + sy.toFixed(4) + ')';

      // Обратная трансформация: стекло тянется, а содержимое под ним остаётся
      // на месте. Иначе копия разъезжается с оригиналом, и сразу видно, что
      // под линзой не тот же самый DOM.
      //
      // Висит она на зеркале, а НЕ на слое с фильтром. Причина та же: у
      // WebKit фильтр и собственная трансформация на одном элементе уживаются
      // плохо. Гасим вокруг центра линзы — того же, вокруг которого растёт
      // сама линза, иначе взаимного погашения не выйдет.
      // Запас вычитается здесь: содержимое зеркала отодвинуто внутрь рамкой,
      // и без поправки копия уехала бы на её толщину.
      var cx = geo.w / 2, cy = geo.h / 2;
      el.mirror.style.transform =
        'translate(' + cx.toFixed(2) + 'px,' + cy.toFixed(2) + 'px) '
        + 'scale(' + (1 / sx).toFixed(4) + ',' + (1 / sy).toFixed(4) + ') '
        + 'translate(' + (-cx).toFixed(2) + 'px,' + (-cy).toFixed(2) + 'px) '
        + 'translate(' + (geo.mx - geo.pad - tx).toFixed(2) + 'px,'
        + (geo.my - geo.pad).toFixed(2) + 'px)';

      // Цвет ПОД стеклом даёт сама линза: в копии активны все вкладки, а видно
      // из неё ровно то, что она накрыла. Тут ничего делать не нужно.
      // Остаётся строка СНАРУЖИ линзы — ею занимается syncRowActive.
      syncRowActive();

      if (path === 'mirror' && GLASS.pressPlate > 0) {
        // Плашка подаётся только в режиме зеркала: в Chromium трансформация
        // предка создаёт «корень фона» и убивает backdrop-filter у линзы.
        var plateT = sim.press > 0.001
          ? 'scale(' + (1 + sim.press * GLASS.pressPlate).toFixed(4) + ')' : '';
        // Пишем только на изменение: в покое и на ходу press равен нулю, а
        // присвоение стиля каждый кадр всё равно сбрасывает расчёт стилей.
        if (plateT !== lastPlateT) {
          el.plate.style.transform = plateT;
          lastPlateT = plateT;
        }
      }
      applyRefraction();
    }

    /** Какая вкладка горит СНАРУЖИ линзы. Пока линза едет или её ведут
     *  пальцем — никакая: цвет в это время несёт только стекло, а прежняя
     *  вкладка гореть не должна, она уже не выбрана. Загорается та, на
     *  которой линза остановилась.
     *
     *  Под стеклом всё это ни на что не влияет: там активны все вкладки. */
    function rowActive() {
      if (dragging) return -1;
      // Смотрим только на движение и не трогаем пружину нажатия: иначе
      // простое касание линзы без ведения гасило бы вкладку и зажигало
      // обратно на отпускании. Порог по скорости даёт вкладке загореться на
      // излёте, чуть раньше полной остановки, — так отзывчивее.
      if (Math.abs(sim.target - sim.pos) > 0.02 || Math.abs(sim.vel) > 0.05) return -1;
      return clamp(Math.round(sim.pos), 0, tabs.length - 1);
    }

    function syncRowActive() {
      var at = rowActive();
      for (var i = 0; i < tabs.length; i++) {
        // toggle без изменения ничего не трогает, поэтому наблюдателя копии
        // это будит ровно дважды за переезд: на старте и на финише.
        tabs[i].classList.toggle('is-active', i === at);
      }
    }

    function atRest() {
      // Пока ведут пальцем, покоя нет по определению: цикл должен крутиться,
      // иначе линза замирала бы между событиями движения.
      if (dragging) return false;
      return Math.abs(sim.target - sim.pos) < 0.0004 && Math.abs(sim.vel) < 0.0015
        && Math.abs(sim.form) < 0.0006 && Math.abs(sim.formVel) < 0.002
        && Math.abs(sim.pressTarget - sim.press) < 0.001 && Math.abs(sim.pressVel) < 0.002;
    }

    function step(now) {
      frame = 0;
      var dt = lastTime ? (now - lastTime) / 1000 : 1 / 60;
      lastTime = now;
      // Приложение провисело в фоне — не даём пружине улететь на огромном шаге.
      dt = clamp(dt, 1 / 240, 1 / 30);

      if (dragging) {
        // Под пальцем пружины нет вовсе: положение уже поставлено обработчиком
        // движения, ровно там, где палец. Прямое управление отставать не имеет
        // права — пружина при постоянной скорости отстаёт на posC/posK, это
        // около сотой доли секунды и на глаз читается как задержка.
        //
        // Скорость всё равно нужна: ею кормится деформация, и она же достаётся
        // пружине на отпускании, отсюда бросок. Меряем по фактическому ходу за
        // кадр, а не по событиям пальца: когда палец замирает, событий нет, а
        // скорость должна честно упасть в ноль.
        sim.vel = (sim.pos - dragPrev) / dt;
        dragPrev = sim.pos;
      } else {
        // Позиция. Полунеявный Эйлер: устойчив на таких жёсткостях и стоит
        // четыре умножения.
        sim.vel += (-GLASS.posK * (sim.pos - sim.target) - GLASS.posC * sim.vel) * dt;
        sim.pos += sim.vel * dt;
      }

      // Деформация. Цель задаёт скорость, но идёт к ней своя пружина со слабым
      // демпфированием: после остановки она проскакивает ноль и качается — это
      // и есть wobble. Без отдельной пружины растяжение гасло бы вместе со
      // скоростью, и стекло было бы мёртвым.
      var want = clamp(Math.abs(sim.vel) * GLASS.stretchGain, 0, GLASS.stretchMax);
      sim.formVel += (-GLASS.formK * (sim.form - want) - GLASS.formC * sim.formVel) * dt;
      sim.form += sim.formVel * dt;

      sim.pressVel += (-GLASS.pressK * (sim.press - sim.pressTarget)
        - GLASS.pressC * sim.pressVel) * dt;
      sim.press += sim.pressVel * dt;

      paint();

      if (atRest()) {
        sim.pos = sim.target; sim.vel = 0;
        sim.form = 0; sim.formVel = 0;
        sim.press = sim.pressTarget; sim.pressVel = 0;
        lastTime = 0;
        paint();
        return;
      }
      frame = requestAnimationFrame(step);
    }

    function run() {
      if (frame || !geo) return;
      lastTime = 0;
      frame = requestAnimationFrame(step);
    }

    /* --- жесты ---------------------------------------------------------- */
    /* Тап по разделу переключает его прежним обработчиком click — routing не
       трогали вовсе. Здесь только палец, легший на саму линзу: он её тащит.
       Порог в шесть пикселей отделяет перетаскивание от тапа, иначе дрожь
       руки превращала бы каждое нажатие в жест. */
    function initGestures() {
      if (!window.PointerEvent) return;
      var drag = null;
      var swallowClick = false;

      /** Положение линзы (в номерах вкладок), при котором её середина
       *  оказывается в точке clientX. geo.left отсчитан от padding box
       *  плашки — рамку добавляем обратно. */
      function trackAt(clientX) {
        var plate = el.plate.getBoundingClientRect();
        return clamp(
          (clientX - plate.left - geo.border - geo.left - geo.w / 2) / geo.step,
          0, tabs.length - 1);
      }

      el.plate.addEventListener('pointerdown', function (event) {
        if (!geo) return;
        var box = el.lens.getBoundingClientRect();
        // Гасим флаг здесь: если прошлый жест оборвала система, click после
        // него не пришёл, и без сброса флаг съел бы следующий тап.
        swallowClick = false;
        // Начинаем только с самой линзы: нажатие по соседнему разделу — тап,
        // его доведёт обычный click на кнопке.
        if (event.clientX < box.left || event.clientX > box.right) return;
        drag = {
          id: event.pointerId, startX: event.clientX, lastX: event.clientX, moved: false,
          // За какую точку линзы взялись. Без этого линза при первом же
          // движении прыгает серединой под палец, даже если взяли за край.
          grab: event.clientX - (box.left + box.width / 2)
        };
        sim.pressTarget = 1;
        el.tabbar.classList.add('is-pressed');
        run();
      });

      el.plate.addEventListener('pointermove', function (event) {
        if (!drag || event.pointerId !== drag.id) return;
        if (!drag.moved) {
          if (Math.abs(event.clientX - drag.startX) < GLASS.dragThreshold) return;
          drag.moved = true;
          dragging = true;
          dragPrev = sim.pos;
          // Захват на плашке, а не на линзе: у линзы pointer-events: none.
          try { el.plate.setPointerCapture(event.pointerId); } catch (e) { /* старый WebView */ }
        }
        drag.lastX = event.clientX;
        // Линза идёт ровно за пальцем, один к одному. Раньше здесь велась
        // цель, а линзу тянула к ней пружина — отсюда и бралось отставание.
        sim.pos = sim.target = trackAt(event.clientX - drag.grab);
        run();
        event.preventDefault();
      });

      function release(event) {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        var moved = drag.moved;
        var x = drag.lastX - drag.grab;   // pointercancel приходит без координат
        drag = null;
        dragging = false;
        sim.pressTarget = 0;
        el.tabbar.classList.remove('is-pressed');
        run();
        if (!moved) return;   // это был тап, его доведёт click

        var index = clamp(Math.round(trackAt(x)), 0, tabs.length - 1);
        sim.target = index;
        // Браузер всё равно пришлёт click по разделу, над которым оторвался
        // палец, — он может не совпасть с тем, куда доехала линза.
        swallowClick = true;
        if (onSelect) onSelect(index);
      }

      el.plate.addEventListener('pointerup', release);
      el.plate.addEventListener('pointercancel', release);
      el.plate.addEventListener('click', function (event) {
        if (!swallowClick) return;
        swallowClick = false;
        event.stopPropagation();
        event.preventDefault();
      }, true);
    }

    /* --- размеры окна ---------------------------------------------------- */
    /* Плашка стоит на env(safe-area-inset-bottom), но в Telegram этого мало:
       клиент меняет высоту вьюпорта сам, и на каждый пересчёт надо
       переизмериться, иначе линза считает шаг по старой ширине. */
    function watchViewport() {
      var pending = false;
      function remeasure() {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () {
          pending = false;
          // Под пальцем плашка отмасштабирована, и её прямоугольник врёт:
          // шаг раздела вышел бы больше настоящего. Ждём, пока отпустят.
          if (sim.press > 0.01) { requestAnimationFrame(remeasure); return; }
          if (measure()) paint();
        });
      }
      if (window.ResizeObserver) new ResizeObserver(remeasure).observe(el.plate);
      window.addEventListener('resize', remeasure);
      window.addEventListener('orientationchange', remeasure);
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', remeasure);
      }
      try {
        if (tg && tg.onEvent) tg.onEvent('viewportChanged', remeasure);
      } catch (e) { /* версия клиента без событий */ }
    }

    /* --- выбор пути ------------------------------------------------------ */
    /* Честно: живой пробы здесь нет и быть не может. Отрисованные пиксели без
       скриншота из DOM не прочитать, а CSS.supports про `backdrop-filter:
       url()` в WebKit отвечает «да» и врёт — ровно та ловушка, на которой
       сгорела прошлая реализация. Поэтому движок определяется по строке
       клиента, и по умолчанию берётся зеркало: оно работает везде.
       `backdrop-filter` включается только там, где мы уверены, что Chromium.
       Перебрать вручную при отладке: window.__tabbarPath = 'mirror'. */
    function pickPath() {
      if (window.__tabbarPath) return window.__tabbarPath;
      var ua = navigator.userAgent || '';
      var apple = /iPhone|iPad|iPod/.test(ua)
        || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
      var chromium = /Chrome\/|Chromium\/|Edg\/|OPR\//.test(ua);
      if (apple || !chromium) return 'mirror';
      if (window.CSS && CSS.supports
        && !CSS.supports('backdrop-filter', 'url(#x)')
        && !CSS.supports('-webkit-backdrop-filter', 'url(#x)')) return 'mirror';
      return 'backdrop';
    }

    /* --- сборка ---------------------------------------------------------- */

    /** Переопределения настроек стекла из localStorage. Нужны затем, что
     *  единственный судья этому эффекту — живое устройство: ни headless, ни
     *  Chromium не показывают ни силы преломления, ни настоящей цены кадра.
     *  Страница проверки пишет сюда набор чисел, приложение их подхватывает,
     *  и подбор настройки перестаёт стоить деплоя за каждое значение.
     *  Берём только те ключи, что уже есть в GLASS, и только числа. */
    function readOverrides() {
      try {
        var raw = window.localStorage && localStorage.getItem('tabbarGlass');
        if (!raw) return;
        var over = JSON.parse(raw);
        for (var key in over) {
          if (Object.prototype.hasOwnProperty.call(over, key)
            && Object.prototype.hasOwnProperty.call(GLASS, key)
            && typeof over[key] === 'number' && isFinite(over[key])) {
            GLASS[key] = over[key];
          }
        }
      } catch (e) { /* приватный режим или испорченный JSON */ }
    }

    function init(tabbar, tabNodes, select) {
      if (!tabbar || !tabNodes || !tabNodes.length) return;
      readOverrides();
      el = {
        tabbar: tabbar,
        plate: tabbar.querySelector('.tabbar__plate'),
        row: tabbar.querySelector('.tabbar__row'),
        lens: tabbar.querySelector('.tabbar__lens'),
        fx: tabbar.querySelector('.tabbar__fx'),
        mirror: tabbar.querySelector('.tabbar__mirror')
      };
      if (!el.plate || !el.row || !el.lens || !el.fx || !el.mirror) { el = null; return; }

      tabs = Array.prototype.slice.call(tabNodes);
      onSelect = select || null;
      path = pickPath();
      tabbar.classList.toggle('is-mirror', path === 'mirror');
      window.__tabbarPathUsed = path;   // видно из консоли при отладке

      try {
        reduced = !!(window.matchMedia
          && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      } catch (e) { reduced = false; }

      var svg = svgNode('svg', { width: 0, height: 0, 'aria-hidden': 'true', focusable: 'false' });
      svg.setAttribute('class', 'tabbar__defs');
      var defs = svgNode('defs', {});
      svg.appendChild(defs);
      document.body.appendChild(svg);
      fx = { defs: defs, filter: null, id: null, disp: [], w: 0, h: 0, version: 0 };

      if (path === 'mirror') { syncMirror(); watchMirror(); }
      measure();
      paint();
      initGestures();
      watchViewport();
    }

    /** Ведёт линзу к разделу. Вызывается из render() на каждой перерисовке,
     *  поэтому повторный вызов с прежней целью не должен ничего будить. */
    function select(index) {
      if (!el) return;
      var at = clamp(index, 0, Math.max(0, tabs.length - 1));
      // Пока палец ведёт линзу, render() ей не указ: иначе случайная
      // перерисовка посреди жеста дёрнула бы её обратно.
      if (dragging) return;
      var first = !booted;
      booted = true;
      if (sim.target === at && !first) return;
      sim.target = at;
      if (!geo && !measure()) { sim.pos = at; return; }
      // Первый вызов — восстановление раздела из настроек на запуске. Линза
      // должна уже стоять на нём, а не приезжать через весь таббар на глазах.
      if (first || reduced) {
        sim.pos = at; sim.vel = 0; sim.form = 0; sim.formVel = 0;
        paint();
        return;
      }
      // Гасим строку сразу, не дожидаясь первого кадра: иначе прежняя вкладка
      // ещё один кадр горит, когда линза уже тронулась.
      syncRowActive();
      run();
    }

    return { init: init, select: select };
  })();

  /* ------------------------------------------------------------------ */
  /* Запуск                                                             */
  /* ------------------------------------------------------------------ */

  function init() {
    dom.app = document.getElementById('app');
    dom.screen = document.getElementById('screen');
    dom.title = document.getElementById('header-title');
    dom.sub = document.getElementById('header-sub');
    dom.back = document.getElementById('back');
    dom.tabbar = document.getElementById('tabbar');
    // Только прямые потомки строки: у зеркала внутри линзы лежат клоны с теми
    // же классами, и без этого уточнения они попали бы в выборку.
    dom.tabs = dom.tabbar.querySelectorAll('.tabbar__plate > .tabbar__row > .tab');
    dom.dialogRoot = document.getElementById('dialog-root');
    dom.toast = document.getElementById('toast');

    dom.back.addEventListener('click', goBack);
    Array.prototype.forEach.call(dom.tabs, function (tab) {
      tab.addEventListener('click', function () { setTab(tab.getAttribute('data-tab')); });
    });
    TabBar.init(dom.tabbar, dom.tabs, function (index) {
      var tab = dom.tabs[index];
      if (tab) setTab(tab.getAttribute('data-tab'));
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
