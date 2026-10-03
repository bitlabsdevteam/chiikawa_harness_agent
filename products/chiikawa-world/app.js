'use strict';

(() => {
  const header = document.querySelector('.site-header');
  const menu = document.querySelector('.menu-toggle');
  const navigation = document.querySelector('#navigation');
  menu.hidden = false;
  header.classList.add('menu-enabled');
  function closeMenu() {
    navigation.classList.remove('is-open');
    menu.setAttribute('aria-expanded', 'false');
  }
  menu.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(open));
    navigation.classList.toggle('is-open', open);
  });
  navigation.addEventListener('click', event => {
    if (event.target.closest('a')) closeMenu();
  });
  header.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') {
      closeMenu();
      menu.focus();
    }
  });

  const profiles = {
    chiikawa: {
      name: 'Chiikawa', label: 'The tender heart · ちいかわ',
      description: 'Gentle, shy, and easily moved to tears, Chiikawa finds courage in the company of good friends. Whether facing a tricky challenge or enjoying a well-earned treat, this little friend reminds us that trying is a brave thing all on its own.',
      lesson: 'A little reminder: you can be scared and still be brave.'
    },
    hachiware: {
      name: 'Hachiware', label: 'The sunny soul · ハチワレ',
      description: 'Chatty, curious, and full of optimism, Hachiware can find a bright side in just about anything. With distinctive blue markings and a generous heart, this friend is always ready to share a discovery, a song, or a little encouragement.',
      lesson: 'A little reminder: the good things are even better shared.'
    },
    usagi: {
      name: 'Usagi', label: 'The wild little spirit · うさぎ',
      description: 'A burst of energy with very long ears! Usagi follows curiosity wherever it leads, usually with an unexpected shout and a healthy appetite. Mischievous, spontaneous, and impossible to predict, this friend makes ordinary days an adventure.',
      lesson: 'A little reminder: leave a little room for the unexpected.'
    }
  };
  const dialog = document.querySelector('#profile-dialog');
  if (typeof dialog.showModal === 'function') {
    document.querySelectorAll('[data-profile]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => {
        const id = button.dataset.profile;
        const profile = profiles[id];
        document.querySelector('#profile-art use').setAttribute('href', `#${id}`);
        document.querySelector('#profile-title').textContent = profile.name;
        document.querySelector('#profile-label').textContent = profile.label;
        document.querySelector('#profile-description').textContent = profile.description;
        document.querySelector('#profile-lesson').textContent = profile.lesson;
        dialog.showModal();
      });
    });
    dialog.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('click', event => {
      const bounds = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
    });
  }

  // Keep only this site's three task IDs and local calendar day, never personal data.
  const storageKey = 'chiikawa-world:little-joys:v1';
  const tasks = [...document.querySelectorAll('input[name="joy"]')];
  const allowedTasks = new Set(tasks.map(task => task.value));
  const localDay = () => {
    const now = new Date();
    return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
  };
  let day = localDay();
  let completed = new Set();
  const storageNote = document.querySelector('#storage-note');
  function reportStorageUnavailable() {
    storageNote.textContent = 'Your checklist works here, but this browser couldn’t save it.';
  }
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw !== null) {
      const saved = JSON.parse(raw);
      if (saved && saved.day === day && Array.isArray(saved.completed)) {
        completed = new Set(saved.completed.filter(id => allowedTasks.has(id)));
      }
    }
  } catch {
    // Blocked storage or stale, malformed data must not disable the checklist.
    reportStorageUnavailable();
  }
  function renderJoys() {
    tasks.forEach(task => { task.checked = completed.has(task.value); });
    const count = completed.size;
    document.querySelector('#joy-progress').textContent = count === 3
      ? '3 of 3 little joys. Look at you, making today lovely!'
      : `${count} of 3 little joys collected today.`;
    document.querySelectorAll('.progress-dots span').forEach((dot, index) => {
      dot.classList.toggle('complete', index < count);
    });
  }
  function resetIfNewDay() {
    if (day !== localDay()) {
      day = localDay();
      completed.clear();
      renderJoys();
    }
  }
  tasks.forEach(task => {
    task.disabled = false;
    task.addEventListener('change', () => {
      const checked = task.checked;
      resetIfNewDay();
      if (checked) completed.add(task.value);
      else completed.delete(task.value);
      renderJoys();
      try {
        localStorage.setItem(storageKey, JSON.stringify({ day, completed: [...completed] }));
        storageNote.textContent = 'Saved only in this browser. A fresh start each day.';
      } catch {
        reportStorageUnavailable();
      }
    });
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) resetIfNewDay();
  });
  renderJoys();

  const reminders = [
    'You don’t have to be big to make someone’s day.',
    'A slow day is still a day worth being kind to yourself.',
    'Some adventures begin with a deep breath and a small step.',
    'You’re allowed to be a little proud of the little things.',
    'The world is softer with a good friend beside you.',
    'Rest is part of the adventure, too.',
    'There is no wrong pace for finding your own little happy.'
  ];
  let reminderIndex = 0;
  const reminderButton = document.querySelector('#new-wish');
  reminderButton.hidden = false;
  reminderButton.addEventListener('click', () => {
    // Select any other reminder so successive clicks always offer something new.
    reminderIndex = (reminderIndex + 1 + Math.floor(Math.random() * (reminders.length - 1))) % reminders.length;
    document.querySelector('#wish-title').textContent = reminders[reminderIndex];
  });
})();
