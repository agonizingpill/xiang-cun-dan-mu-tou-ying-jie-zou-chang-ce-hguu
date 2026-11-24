import {workspace} from '../workspace.js';
import {el, display} from '../dom.js';
import {status} from '../widgets.js';

const page = {
  "title": "对场合用与分路提示",
  "module": "交场分路回单",
  "caption": "交场分路回单",
  "detail": "联动岗按簇内拍点相似度挑试投场地，自动跳过未验收、异协议和占满分路；争路时顺延，无路可走待重试且不占线。",
  "path": "/cue_routing",
  "columns": [
    {"name": "code", "label": "编号"},
    {"name": "cluster", "label": "节奏簇"},
    {"name": "destination", "label": "目标场地"},
    {"name": "route", "label": "物理分路"},
    {"name": "status", "label": "交场状态"},
    {"name": "basis", "label": "拍点依据"}
  ],
  "layout": "flip",
  "view": "stack",
  "actions": [
    {
      "name": "apply_routing",
      "label": "按相似度申请试投分路",
      "path": "/cue_routing/apply-routing",
      "fields": [
        {"name": "code", "label": "分路回单", "kind": "text", "initial": "ROUTE-24"},
        {"name": "cluster", "label": "节奏簇", "kind": "text", "initial": "BEAT-24"}
      ],
      "help": "按拍点相似度顺次挑同簇场地，自动跳过未验收、异协议、占满路线；同号重试不重复占线。",
      "roles": ["operator", "reviewer"],
      "success": "已写入"
    },
    {
      "name": "routing_candidates",
      "label": "预览同簇候选分路",
      "path": "/cue_routing/routing-candidates",
      "fields": [
        {"name": "cluster", "label": "节奏簇", "kind": "text", "initial": "BEAT-24"}
      ],
      "help": "只查不占线：列出相似度排序、空闲分路和每条跳过原因。",
      "roles": ["operator", "reviewer", "viewer"],
      "success": "已查询"
    },
    {
      "name": "routing_evidence",
      "label": "查原拍点依据",
      "path": "/cue_routing/routing-evidence",
      "fields": [
        {"name": "code", "label": "回单编号", "kind": "text", "initial": "ROUTE-24"}
      ],
      "help": "已签申请重试后仍可查当时的相似度候选与跳过原因。",
      "roles": ["operator", "reviewer", "viewer"],
      "success": "已查询"
    },
    {
      "name": "share_cue",
      "label": "签回跨场合用",
      "path": "/cue_routing/share-cue",
      "fields": [
        {"name": "code", "label": "分路回单", "kind": "text", "initial": "ROUTE-30"},
        {"name": "cluster", "label": "节奏簇", "kind": "text", "initial": "BEAT-24"},
        {"name": "destination", "label": "目的场地", "kind": "text", "initial": "HALL-F"},
        {"name": "route", "label": "分路", "kind": "text", "initial": "F-2"}
      ],
      "help": "签认前核对不同场地的计量协议。",
      "roles": ["reviewer"],
      "success": "已写入"
    },
    {
      "name": "record_separate",
      "label": "记录异协议分路提示",
      "path": "/cue_routing/record-separate",
      "fields": [
        {"name": "code", "label": "提醒编号", "kind": "text", "initial": "ROUTE-31"},
        {"name": "cluster", "label": "节奏簇", "kind": "text", "initial": "BEAT-24"},
        {"name": "destination", "label": "目的场地", "kind": "text", "initial": "HALL-B"},
        {"name": "route", "label": "本地替代分路", "kind": "text", "initial": "B-2"}
      ],
      "help": "保存本场分路编号给当地值守。",
      "roles": ["operator", "reviewer"],
      "success": "已写入"
    }
  ]
};

function candidateCard(item, index) {
  const usable = item.skipped.length === 0;
  return el('article', {class: 'record' + (usable ? '' : ' muted')},
    el('h3', {}, (index + 1) + '. ' + item.venue + (usable ? '（可试投）' : '（跳过）')),
    el('dl', {},
      el('dt', {}, '拍点相似度'), el('dd', {}, String(item.similarity)),
      el('dt', {}, '依据轨迹'), el('dd', {}, display(item.evidence_tracks)),
      el('dt', {}, 'DTW 距离'), el('dd', {}, display(item.distances)),
      el('dt', {}, '空闲分路'), el('dd', {}, item.free_routes.length ? display(item.free_routes) : '无'),
      el('dt', {}, '跳过原因'), el('dd', {}, item.skipped.length ? display(item.skipped) : '—')));
}

function candidatePanel(payload, kind) {
  if (kind === 'evidence') {
    const basis = payload.basis;
    if (!basis) return status('该回单没有留存拍点依据。', 'error');
    const head = el('h2', {}, '回单 ' + payload.code + ' 的原拍点依据 · ' + payload.status);
    const chosen = basis.chosen
      ? status('选中 ' + basis.chosen.venue + ' 分路 ' + basis.chosen.route +
               '（相似度 ' + basis.chosen.similarity + '），凭据 ' + basis.cluster_token, 'ok')
      : status('无路可用，状态待重试、不占线；凭据 ' + basis.cluster_token, 'error');
    return el('section', {class: 'ledger candidate-panel'}, head,
      status('源场地 ' + basis.origin + ' · 输入轨迹 ' + basis.source_track), chosen,
      el('div', {class: 'records grid'}, basis.candidates.map((x, i) => candidateCard(x, i))));
  }
  const head = el('h2', {}, '节奏簇 ' + payload.cluster + ' 的同簇候选分路');
  const meta = status('源场地 ' + payload.origin + ' · 输入轨迹 ' + payload.source_track +
                      ' · 计算凭据 ' + payload.token);
  return el('section', {class: 'ledger candidate-panel'}, head, meta,
    el('div', {class: 'records grid'},
      payload.candidates.length
        ? payload.candidates.map((x, i) => candidateCard(x, i))
        : status('同簇内没有其他场地的轨迹可作试投候选。', 'error')));
}

export const {title} = page;
export const key = 'cue_routing';

page.extras = (root, {request: apiRequest, el: makeEl}) => {
  const panelHost = makeEl('div', {class: 'candidate-host'});
  root.append(panelHost);

  root.querySelectorAll('form.action').forEach(form => {
    if (!['routing_candidates', 'routing_evidence'].includes(form.id)) return;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const notice = form.querySelector('.status');
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        const data = Object.fromEntries(new FormData(form).entries());
        const payload = await apiRequest(
          page.actions.find(a => a.name === form.id).path, data);
        panelHost.replaceChildren(candidatePanel(payload,
          form.id === 'routing_evidence' ? 'evidence' : 'candidates'));
        notice.textContent = '已查询 · ' + display(payload.code ?? payload.cluster);
        notice.className = 'status ok';
      } catch (error) {
        notice.textContent = error.message;
        notice.className = 'status error';
      } finally {
        button.disabled = false;
      }
    }, true);
  });
};

export async function render(root, role) {
  await workspace(root, role, page);
}
