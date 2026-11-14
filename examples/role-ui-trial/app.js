const params = new URLSearchParams(location.search);
const setting = (key, allowed, fallback) => allowed.includes(params.get(key)) ? params.get(key) : fallback;
const revision = setting('revision', ['baseline', 'fixed'], 'fixed');
const dataset = setting('data', ['demo', 'worst', 'empty', 'one', 'huge'], 'demo');
const theme = setting('theme', ['light', 'dark'], 'light');
const direction = setting('dir', ['ltr', 'rtl'], 'ltr');
const text = setting('text', ['normal', 'large'], 'normal');
document.body.className = revision;
document.documentElement.dataset.theme = theme;
document.documentElement.dataset.text = text;
document.documentElement.dir = direction;
for (const [id, key, value] of [['revision', 'revision', revision], ['dataset', 'data', dataset], ['theme', 'theme', theme], ['direction', 'dir', direction], ['text-size', 'text', text]]) {
  const control = document.getElementById(id);
  control.value = value;
  control.addEventListener('change', () => {
    const url = new URL(location.href);
    url.searchParams.set(key, control.value);
    location.assign(url);
  });
}

const demo = [
  {name:'가상 팀원 A', email:'member-a@example.com', role:'기획'},
  {name:'가상 팀원 B', email:'member-b@example.com', role:'개발'},
  {name:'가상 팀원 C', email:'member-c@example.com', role:null},
  {name:'가상 팀원 D', email:'member-d@example.com', role:'디자인'},
  {name:'가상 팀원 E', email:'member-e@example.com', role:'검토'},
];
const worst = [
  {name:'가상 협업 담당자 — 긴 프로젝트 이름을 사용하는 국제 공동 연구팀', email:'collaboration.coordination@long-project-workspace.example.com', role:'국제 협력과 프로젝트 운영'},
  {name:'Jo', email:'jo@example.com', role:null},
  {name:'🧑🏽‍💻 가상 개발자', email:'frontend.validation@example.com', role:'접근성 검토'},
  {name:'مستخدم تجريبي', email:'rtl.member@example.com', role:'اختبار'},
  {name:'<b>가상 문자열</b>', email:'literal.text@example.com', role:null},
];
const people = dataset === 'empty' ? [] : dataset === 'one' ? demo.slice(0,1) : dataset === 'worst' ? worst : dataset === 'huge' ? Array.from({length:1000}, (_, i) => ({...demo[i % demo.length], name:`가상 팀원 ${i + 1}`})) : demo;
const firstPage = dataset === 'huge' ? 20 : 3;
let limit = firstPage;
const list = document.getElementById('members');
const more = document.getElementById('more');
const node = (tag, className, content) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
};
function renderPeople() {
  document.getElementById('member-count').textContent = `${people.length.toLocaleString('ko-KR')}명`;
  document.getElementById('empty').hidden = people.length !== 0;
  list.replaceChildren(...people.slice(0, limit).map(person => {
    const row = node('li', 'member');
    const avatar = node('span', 'avatar', [...new Intl.Segmenter('ko', {granularity:'grapheme'}).segment(person.name)][0]?.segment || '·');
    avatar.setAttribute('aria-hidden', 'true');
    const copy = node('div', 'copy');
    copy.append(node('div', 'member-name', person.name), node('div', 'member-email', person.email));
    if (person.role) copy.append(node('div', 'member-role', person.role));
    const action = node('button', 'row-action secondary', '열기');
    action.type = 'button';
    action.setAttribute('aria-label', `${person.name} 정보 열기`);
    action.addEventListener('click', () => { document.getElementById('member-detail').textContent = `${person.name} · ${person.email}`; });
    row.append(avatar, copy, action);
    return row;
  }));
  more.hidden = people.length <= firstPage || (revision === 'baseline' && limit >= people.length);
  more.textContent = limit >= people.length ? '목록 접기' : '목록 더 보기';
  more.setAttribute('aria-expanded', String(limit > firstPage));
}
more.addEventListener('click', () => {
  limit = limit >= people.length ? firstPage : Math.min(people.length, limit + firstPage);
  renderPeople();
});
renderPeople();

const form = document.getElementById('task-form');
const title = document.getElementById('task-title');
const save = document.getElementById('save');
const status = document.getElementById('save-status');
let pending = false;
async function refresh() {
  const response = await fetch('/api/tasks');
  if (!response.ok) throw new Error('저장 결과를 조회하지 못했습니다. 다시 조회하세요.');
  const data = await response.json();
  document.getElementById('saved-tasks').replaceChildren(...data.tasks.map(task => node('li', '', task.title)));
  document.getElementById('api-count').textContent = `API 요청 ${data.requests}회 · 저장 ${data.tasks.length}개`;
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (revision === 'fixed' && pending) return;
  pending = true;
  const value = title.value;
  const returnFocus = revision === 'fixed' && document.activeElement === save;
  if (revision === 'fixed') save.disabled = true;
  save.textContent = '저장 중…';
  status.dataset.error = 'false';
  status.textContent = '저장 요청을 보내고 있습니다.';
  try {
    const response = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:value})});
    if (!response.ok) throw new Error('저장하지 못했습니다. 내용을 확인하고 다시 시도하세요.');
    title.value = '';
    status.textContent = '할 일을 저장했습니다.';
  } catch (error) {
    // A known baseline regression: failed requests used to discard the draft.
    if (revision === 'baseline') title.value = '';
    status.dataset.error = 'true';
    status.textContent = error.message;
  } finally {
    pending = false;
    save.disabled = false;
    save.textContent = '할 일 저장';
    if (returnFocus && document.activeElement === document.body) save.focus({preventScroll:true});
    await refresh().catch(error => { status.textContent = error.message; });
  }
});
document.getElementById('refresh').addEventListener('click', () => refresh().catch(error => { status.textContent = error.message; }));
document.getElementById('reset').addEventListener('click', async () => {
  const response = await fetch('/api/reset', {method:'POST'});
  if (!response.ok) throw new Error('시험 API 초기화 실패');
  title.value = '';
  status.textContent = '';
  await refresh();
});
refresh().catch(error => { status.textContent = error.message; });
