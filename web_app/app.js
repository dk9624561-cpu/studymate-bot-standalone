// KGS IAS Web Bot App Engine

const API_ENDPOINTS = {
  BATCHES: 'https://sunnyji7256.github.io/kgs_batch_list/New_Sunny.json',
  CLASSROOM: (batchId) => `https://study-mate.in/api/classroom/${batchId}`,
  LESSON: (topicId) => `https://study-mate.in/api/lesson/${topicId}`,
  VIDEO: (videoId) => `https://study-mate.in/api/video/${videoId}`
};

const LOCAL_PROXY = (typeof window !== 'undefined' && window.location && window.location.origin && window.location.origin !== 'null' && window.location.origin.startsWith('http')) ? window.location.origin : 'http://localhost:3000';

async function fetchWithFallback(primaryUrl, fallbackPath, options = {}) {
  try {
    const res = await fetch(primaryUrl, options);
    if (res.ok) return await res.json();
  } catch (e) {
    console.warn(`Primary API call failed for ${primaryUrl}. Falling back to proxy...`, e);
  }
  const proxyUrl = `${LOCAL_PROXY}${fallbackPath}`;
  const proxyRes = await fetch(proxyUrl, options);
  if (!proxyRes.ok) throw new Error(`Fallback proxy failed: ${proxyRes.status}`);
  return await proxyRes.json();
}

const state = {
  courses: [],
  filteredCourses: [],
  currentStep: 'courses',
  selectedCourse: null,
  selectedTopic: null,
  selectedCategory: 'all',
  searchQuery: ''
};

const viewContainer = document.getElementById('viewContainer');
const searchInput = document.getElementById('searchInput');
const clearSearchBtn = document.getElementById('clearSearchBtn');
const breadcrumbTrail = document.getElementById('breadcrumbTrail');
const itemCount = document.getElementById('itemCount');
const resetBtn = document.getElementById('resetBtn');
const searchSection = document.getElementById('searchSection');

const lectureModal = document.getElementById('lectureModal');
const closeModalBtn = document.getElementById('closeModalBtn');
const modalLectureTitle = document.getElementById('modalLectureTitle');
const modalLoader = document.getElementById('modalLoader');
const modalLinksContainer = document.getElementById('modalLinksContainer');
const toast = document.getElementById('toast');
const toastMsg = document.getElementById('toastMsg');

const DEFAULT_THUMB = 'https://i.postimg.cc/x1M0YN5Z/sunny.jpg';

document.addEventListener('DOMContentLoaded', () => {
  initParticles();
  initEventListeners();
  loadCourses();
});

function initParticles() {
  const container = document.getElementById('particles-container');
  if (!container) return;
  const count = 40;
  for (let i = 0; i < count; i++) {
    const particle = document.createElement('div');
    particle.style.cssText = `
      position: absolute;
      width: ${Math.random() * 4 + 2}px;
      height: ${Math.random() * 4 + 2}px;
      background: ${Math.random() > 0.5 ? 'rgba(124, 247, 255, 0.4)' : 'rgba(178, 124, 255, 0.3)'};
      border-radius: 50%;
      top: ${Math.random() * 100}vh;
      left: ${Math.random() * 100}vw;
      box-shadow: 0 0 10px rgba(124, 247, 255, 0.5);
      animation: floatParticle ${Math.random() * 12 + 10}s infinite ease-in-out;
    `;
    container.appendChild(particle);
  }
}

const styleTag = document.createElement('style');
styleTag.textContent = `
  @keyframes floatParticle {
    0%, 100% { transform: translateY(0) translateX(0); opacity: 0.2; }
    50% { transform: translateY(-40px) translateX(20px); opacity: 0.8; }
  }
`;
document.head.appendChild(styleTag);

function initEventListeners() {
  searchInput.addEventListener('input', (e) => {
    state.searchQuery = e.target.value.toLowerCase().trim();
    clearSearchBtn.style.display = state.searchQuery ? 'block' : 'none';
    if (state.currentStep === 'courses') filterCourses();
  });

  clearSearchBtn.addEventListener('click', () => {
    searchInput.value = '';
    state.searchQuery = '';
    clearSearchBtn.style.display = 'none';
    if (state.currentStep === 'courses') filterCourses();
  });

  document.querySelectorAll('.pill').forEach((pill) => {
    pill.addEventListener('click', (e) => {
      document.querySelectorAll('.pill').forEach((p) => p.classList.remove('active'));
      e.target.classList.add('active');
      state.selectedCategory = e.target.dataset.cat;
      if (state.currentStep === 'courses') filterCourses();
    });
  });

  resetBtn.addEventListener('click', () => navigateToStep('courses'));

  closeModalBtn.addEventListener('click', closeModal);
  lectureModal.addEventListener('click', (e) => {
    if (e.target === lectureModal) closeModal();
  });
}

async function loadCourses() {
  showLoader('Loading 1000+ KGS IAS Courses...');
  try {
    const data = await fetchWithFallback(API_ENDPOINTS.BATCHES, '/api/batches');
    state.courses = data;
    filterCourses();
  } catch (err) {
    console.error(err);
    showError('Unable to load courses. Please check your connection.');
  }
}

function filterCourses() {
  let list = state.courses;

  if (state.selectedCategory === 'kgs') {
    list = list.filter((c) => c.title.toLowerCase().includes('kgs') || c.title.toLowerCase().includes('khan'));
  } else if (state.selectedCategory === 'recent') {
    list = list.slice(0, 150);
  }

  if (state.searchQuery) {
    list = list.filter((c) => c.title.toLowerCase().includes(state.searchQuery));
  }

  state.filteredCourses = list;
  renderCourses();
}

function renderCourses() {
  updateBreadcrumbs();
  searchSection.style.display = 'flex';
  itemCount.textContent = `${state.filteredCourses.length} Courses`;

  if (state.filteredCourses.length === 0) {
    viewContainer.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-folder-open"></i>
        <h3>No courses found matching "${state.searchQuery}"</h3>
        <p>Try searching with another keyword or clear filters.</p>
      </div>
    `;
    return;
  }

  viewContainer.innerHTML = state.filteredCourses.map((c) => `
    <div class="card" onclick="selectCourse(${c.id})">
      <div class="card-img-wrapper">
        <img src="${c.image_thumb || c.image_large || DEFAULT_THUMB}" alt="${escapeHtml(c.title)}" onerror="this.src='${DEFAULT_THUMB}'" />
      </div>
      <div class="card-title">${escapeHtml(c.title)}</div>
      <div class="card-meta">
        <span class="card-badge"><i class="fa-solid fa-graduation-cap"></i> ID: ${c.id}</span>
        <span>Start: ${c.start_at || 'N/A'}</span>
      </div>
    </div>
  `).join('');
}

async function selectCourse(courseId) {
  const course = state.courses.find((c) => String(c.id) === String(courseId));
  if (!course) return;
  state.selectedCourse = course;
  state.currentStep = 'topics';
  updateBreadcrumbs();
  searchSection.style.display = 'none';

  showLoader(`Loading Topics for "${course.title}"...`);

  try {
    const data = await fetchWithFallback(
      API_ENDPOINTS.CLASSROOM(courseId),
      `/api/classroom/${courseId}`,
      { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
    );
    const topics = data.classroom || [];
    renderTopics(topics);
  } catch (err) {
    console.error(err);
    showError('Error loading topics for this course.');
  }
}

function renderTopics(topics) {
  itemCount.textContent = `${topics.length} Topics`;

  if (!topics || topics.length === 0) {
    viewContainer.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-book-open"></i>
        <h3>No Topics Available</h3>
        <p>No classroom topics uploaded for this batch yet.</p>
      </div>
    `;
    return;
  }

  viewContainer.innerHTML = topics.map((t) => `
    <div class="card topic-card" onclick="selectTopic('${t.id}', '${escapeHtml(t.name)}')">
      <div class="topic-icon">
        <i class="fa-solid fa-folder-tree"></i>
      </div>
      <div class="card-title">${escapeHtml(t.name)}</div>
      <div class="card-meta">
        <span class="card-badge"><i class="fa-solid fa-video"></i> ${t.videos || 0} Videos</span>
        <span><i class="fa-solid fa-file-pdf"></i> ${t.notes || 0} Notes</span>
      </div>
    </div>
  `).join('');
}

async function selectTopic(topicId, topicName) {
  state.selectedTopic = { id: topicId, name: topicName };
  state.currentStep = 'lectures';
  updateBreadcrumbs();

  showLoader(`Loading Lectures for "${topicName}"...`);

  try {
    const data = await fetchWithFallback(
      API_ENDPOINTS.LESSON(topicId),
      `/api/lesson/${topicId}`,
      { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
    );
    const videos = data.videos || [];
    const notes = data.notes || [];
    renderLectures(videos, notes);
  } catch (err) {
    console.error(err);
    showError('Error loading lectures for this topic.');
  }
}

function renderLectures(videos, notes) {
  itemCount.textContent = `${videos.length} Lectures | ${notes.length} PDF Notes`;

  if (videos.length === 0 && notes.length === 0) {
    viewContainer.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-film"></i>
        <h3>No Lectures or Notes Found</h3>
        <p>This topic does not contain any videos or pdf files yet.</p>
      </div>
    `;
    return;
  }

  let html = '';

  videos.forEach((v) => {
    html += `
      <div class="card lecture-card" onclick="openLectureLinks('${v.id}', '${escapeHtml(v.name)}')">
        <img class="lecture-thumb" src="${v.thumb || DEFAULT_THUMB}" alt="${escapeHtml(v.name)}" onerror="this.src='${DEFAULT_THUMB}'" />
        <div class="lecture-info">
          <div class="card-title">${escapeHtml(v.name)}</div>
          <div class="card-meta" style="border-top:none; padding-top:0; margin-top:4px;">
            <span class="card-badge"><i class="fa-solid fa-circle-play"></i> Video Lecture</span>
            <span>${v.published_at ? new Date(v.published_at).toLocaleDateString() : ''}</span>
          </div>
        </div>
        <button class="lecture-action-btn">
          <i class="fa-solid fa-link"></i> Get Links
        </button>
      </div>
    `;
  });

  notes.forEach((n) => {
    html += `
      <div class="card lecture-card" onclick="openLectureLinks('${n.id}', '${escapeHtml(n.name)}')">
        <div class="topic-icon" style="width:50px;height:50px;font-size:1.2rem;">
          <i class="fa-solid fa-file-pdf"></i>
        </div>
        <div class="lecture-info">
          <div class="card-title">${escapeHtml(n.name)}</div>
          <div class="card-meta" style="border-top:none; padding-top:0; margin-top:4px;">
            <span class="card-badge" style="background:rgba(255,107,129,0.15);color:var(--accent);"><i class="fa-solid fa-file"></i> Class PDF Note</span>
          </div>
        </div>
        <button class="lecture-action-btn">
          <i class="fa-solid fa-download"></i> View PDF
        </button>
      </div>
    `;
  });

  viewContainer.innerHTML = html;
}

async function openLectureLinks(videoId, videoName) {
  modalLectureTitle.textContent = videoName;
  modalLinksContainer.innerHTML = '';
  modalLoader.style.display = 'flex';
  lectureModal.style.display = 'flex';

  try {
    const data = await fetchWithFallback(
      API_ENDPOINTS.VIDEO(videoId),
      `/api/video/${videoId}`,
      { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
    );
    modalLoader.style.display = 'none';

    let linksHtml = '';

    if (data.hd_video_url) {
      linksHtml += createLinkItemHtml('HD Video Stream Link', data.hd_video_url, 'play');
    }
    if (data.video_url && data.video_url !== data.hd_video_url) {
      linksHtml += createLinkItemHtml('SD Video Stream Link', data.video_url, 'play');
    }
    if (data.pdfs && data.pdfs.length > 0) {
      data.pdfs.forEach((pdf, index) => {
        linksHtml += createLinkItemHtml(`PDF Note: ${pdf.title || 'Document ' + (index + 1)}`, pdf.url, 'pdf');
      });
    }

    if (!linksHtml) {
      linksHtml = `<p style="color:var(--text-muted);text-align:center;padding:20px;">No video stream or PDF links found for this lecture.</p>`;
    }

    modalLinksContainer.innerHTML = linksHtml;
  } catch (err) {
    console.error(err);
    modalLoader.style.display = 'none';
    modalLinksContainer.innerHTML = `<p style="color:var(--accent);text-align:center;padding:20px;">Error fetching links for this lecture.</p>`;
  }
}

function createLinkItemHtml(title, url, type) {
  const icon = type === 'pdf' ? 'fa-file-pdf' : 'fa-circle-play';
  return `
    <div class="link-item">
      <div class="link-title">
        <i class="fa-solid ${icon}" style="color:${type === 'pdf' ? 'var(--accent)' : 'var(--primary)'}"></i>
        <span>${escapeHtml(title)}</span>
      </div>
      <div class="link-actions">
        <button class="link-btn link-btn-copy" onclick="copyToClipboard('${url}')">
          <i class="fa-solid fa-copy"></i> Copy Link
        </button>
        <a class="link-btn link-btn-play" href="${url}" target="_blank">
          <i class="fa-solid ${type === 'pdf' ? 'fa-arrow-right-long' : 'fa-play'}"></i> Open
        </a>
      </div>
    </div>
  `;
}

window.copyToClipboard = function (text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast('Link copied to clipboard!');
  }).catch(() => {
    showToast('Copied link: ' + text);
  });
};

function showToast(msg) {
  toastMsg.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => {
    toast.classList.remove('show');
  }, 2500);
}

function closeModal() {
  lectureModal.style.display = 'none';
}

function updateBreadcrumbs() {
  let trailHtml = `<span class="crumb ${state.currentStep === 'courses' ? 'active' : ''}" onclick="navigateToStep('courses')"><i class="fa-solid fa-graduation-cap"></i> Courses</span>`;

  if (state.selectedCourse && (state.currentStep === 'topics' || state.currentStep === 'lectures')) {
    trailHtml += ` <span class="crumb-separator"><i class="fa-solid fa-chevron-right"></i></span> `;
    trailHtml += `<span class="crumb ${state.currentStep === 'topics' ? 'active' : ''}" onclick="navigateToStep('topics')"><i class="fa-solid fa-folder-tree"></i> ${escapeHtml(state.selectedCourse.title)}</span>`;
  }

  if (state.selectedTopic && state.currentStep === 'lectures') {
    trailHtml += ` <span class="crumb-separator"><i class="fa-solid fa-chevron-right"></i></span> `;
    trailHtml += `<span class="crumb active"><i class="fa-solid fa-film"></i> ${escapeHtml(state.selectedTopic.name)}</span>`;
  }

  breadcrumbTrail.innerHTML = trailHtml;
}

function navigateToStep(step) {
  state.currentStep = step;
  if (step === 'courses') {
    state.selectedCourse = null;
    state.selectedTopic = null;
    filterCourses();
  } else if (step === 'topics' && state.selectedCourse) {
    state.selectedTopic = null;
    selectCourse(state.selectedCourse.id);
  }
}

function showLoader(msg) {
  viewContainer.innerHTML = `
    <div class="loader-box">
      <div class="custom-spinner"></div>
      <p class="loader-text">${msg}</p>
    </div>
  `;
}

function showError(msg) {
  viewContainer.innerHTML = `
    <div class="empty-state">
      <i class="fa-solid fa-triangle-exclamation" style="color:var(--accent)"></i>
      <h3>Something went wrong</h3>
      <p>${msg}</p>
    </div>
  `;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>"']/g, function (m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m];
  });
}
