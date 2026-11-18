/**
 * 演示模式（`?demo=1`）。
 *
 * 用途有两个，都很实际：
 * 1. 答辩现场如果产物侧没起、模型 Key 没配，界面依然能完整演示；
 * 2. 改前端时不用连后端就能核对卡片、表格、图表的渲染。
 *
 * 数据是编的，但结构与真实事件流一致：同一条消息流里依次出现
 * 工具调用轨迹 → 数据卡（表格/图表）→ 助手结论 → 生成计划 → 生成进度。
 */
import { useEventStore } from "../stores/event";

let seed = 1000;
const nextId = () => `d${++seed}`;

const ACTIVITY_COLUMNS = [
  { key: "name", title: "活动名称", type: "VARCHAR" },
  { key: "location", title: "活动地点", type: "VARCHAR" },
  { key: "capacity", title: "活动名额上限", type: "INT" },
  { key: "registered_count", title: "当前已报名人数", type: "INT" },
  { key: "start_time", title: "活动开始时间", type: "DATETIME" },
];

const ACTIVITY_ROWS = [
  { name: "春季读书分享会", location: "图书馆三层报告厅", capacity: 80, registered_count: 64, start_time: "2026-03-14 14:00:00" },
  { name: "校园马拉松", location: "东区田径场", capacity: 500, registered_count: 421, start_time: "2026-04-06 08:00:00" },
  { name: "开源技术沙龙", location: "计算机学院 A301", capacity: 120, registered_count: 118, start_time: "2026-04-19 19:00:00" },
  { name: "毕业季跳蚤市场", location: "南门广场", capacity: 300, registered_count: 156, start_time: "2026-05-24 09:30:00" },
  { name: "夏季摄影展", location: "艺术楼展厅", capacity: 200, registered_count: 87, start_time: "2026-06-02 10:00:00" },
  { name: "期末复习互助", location: "图书馆自习区", capacity: 60, registered_count: 52, start_time: "2026-06-18 18:30:00" },
];

const SIGNUP_COLUMNS = [
  { key: "applicant_name", title: "报名人姓名", type: "VARCHAR" },
  { key: "contact_phone", title: "报名人联系方式", type: "VARCHAR" },
  { key: "status", title: "报名状态", type: "VARCHAR" },
  { key: "remark", title: "报名备注", type: "VARCHAR" },
  { key: "created_at", title: "创建时间", type: "DATETIME" },
];

const SIGNUP_ROWS = [
  { applicant_name: "陈思远", contact_phone: "138-1024-7781", status: "已报名", remark: "", created_at: "2026-03-02 09:12:00" },
  { applicant_name: "林小满", contact_phone: "139-2288-0135", status: "已报名", remark: "需要志愿者服装 L 码", created_at: "2026-03-02 10:41:00" },
  { applicant_name: "赵一鸣", contact_phone: "186-6642-9930", status: "已取消", remark: "时间冲突", created_at: "2026-03-03 08:05:00" },
  { applicant_name: "周雨桐", contact_phone: "137-7710-4422", status: "已报名", remark: "", created_at: "2026-03-03 15:27:00" },
  { applicant_name: "吴嘉禾", contact_phone: "150-9981-2043", status: "已报名", remark: "带一名同伴", created_at: "2026-03-04 11:58:00" },
];

const STATUS_TABLE = {
  entity: "signup",
  display: "报名记录 · 按状态统计",
  kind: "aggregate",
  columns: [
    { key: "status", title: "报名状态", type: "VARCHAR" },
    { key: "cnt", title: "记录数", type: "BIGINT" },
  ],
  rows: [
    { status: "已报名", cnt: 34 },
    { status: "已取消", cnt: 6 },
  ],
  total: 2,
  chart: { dimensions: ["status"], measures: ["cnt"] },
  toolCall: {
    name: "aggregate_records",
    args: { entity: "signup", groupBy: ["status"], metrics: [{ func: "COUNT", alias: "cnt" }] },
  },
};

const PLAN_SUMMARY = `已理解需求，将生成 **3 个实体**：

| 实体 | 字段 | 说明 |
| --- | --- | --- |
| 图书 | 书名、作者、出版社、ISBN、库存册数 | 基础档案 |
| 读者 | 姓名、学号、联系方式、状态 | 借阅人 |
| 借阅记录 | 图书、读者、借出时间、应还时间、归还时间 | 流水 |

配套接口：图书 / 读者 / 借阅记录各 5 个（查询、统计、新增、修改、删除），共 15 个。
建议角色：管理员（全部权限）、馆员（图书与借阅的读写）、读者（只读借阅记录）。`;

const DEMO_REPLY = `演示模式下不会真的调用模型，所以这里没有生成新的回答。

要看真实效果：把地址里的 \`?demo=1\` 去掉，用产物侧的账号登录即可。`;

function message(role, type, content, turn = 1, extra = {}) {
  return { id: nextId(), role, type, content, turn, ...extra };
}

export function seedDemo(chat) {
  const events = useEventStore();

  chat.sessionId = "demo-session";
  chat.title = "活动报名系统 · 看板与生成演示";
  chat.turn = 1;
  chat.sessions = [
    { sessionId: "demo-session", title: "活动报名系统 · 看板与生成演示", lastActiveAt: new Date().toISOString() },
    { sessionId: "demo-old-1", title: "按报名状态统计", lastActiveAt: new Date(Date.now() - 86400000).toISOString() },
    { sessionId: "demo-old-2", title: "做一个图书借阅管理系统", lastActiveAt: new Date(Date.now() - 3 * 86400000).toISOString() },
  ];

  chat.messages = [
    message("user", "text", "看看每个活动的报名情况，并对比一下名额和已报名人数。"),

    message("assistant", "tools", {
      calls: [{
        name: "query_records",
        ok: true,
        args: { entity: "activity", orderBy: [{ field: "registered_count", direction: "DESC" }], size: 20 },
        ms: 38,
      }],
    }),

    message("assistant", "table", {
      entity: "activity",
      display: "活动",
      columns: ACTIVITY_COLUMNS,
      rows: ACTIVITY_ROWS,
      total: 6,
      chart: { dimensions: ["name"], measures: ["capacity", "registered_count"] },
      toolCall: {
        name: "query_records",
        args: { entity: "activity", size: 20 },
      },
    }),

    message("assistant", "text", `共 6 个活动，合计名额 **1260**、已报名 **898** 人，整体占用率约 **71%**。

- **开源技术沙龙**最紧张：120 个名额已报 118，只剩 2 个。
- **夏季摄影展**还有 113 个空位，适合继续推广。
- 需要看具体报名人时，可以继续问「开源技术沙龙的报名记录」。`),

    message("user", "text", "按报名状态统计一下，再列最近 5 条报名记录。"),

    message("assistant", "tools", {
      calls: [
        {
          name: "query_records",
          ok: false,
          args: { entity: "signup", filter: { phone: { eq: "138" } } },
          error: "字段 phone 不在 signup 里。可用：id、activity_id、applicant_name、contact_phone、status、remark、created_at",
          ms: 12,
        },
        {
          name: "aggregate_records",
          ok: true,
          args: { entity: "signup", groupBy: ["status"], metrics: [{ func: "COUNT", alias: "cnt" }] },
          ms: 46,
        },
        {
          name: "query_records",
          ok: true,
          args: { entity: "signup", orderBy: [{ field: "created_at", direction: "DESC" }], size: 5 },
          ms: 51,
        },
      ],
    }),

    message("assistant", "table", STATUS_TABLE),

    message("assistant", "table", {
      entity: "signup",
      display: "报名记录",
      columns: SIGNUP_COLUMNS,
      rows: SIGNUP_ROWS,
      total: 40,
      toolCall: {
        name: "query_records",
        args: { entity: "signup", size: 5 },
      },
    }),

    message("assistant", "text", `有效报名 **34 条**、已取消 **6 条**，取消率约 15%。

第一次查询把「联系方式」写成了 \`phone\`，字段不存在，已按真实字段名 \`contact_phone\` 重试——工具轨迹里能看到这次纠错。`),

    message("user", "text", "把刚才那条删了"),

    message("assistant", "ask", {
      kind: "record",
      text: "最近一次查询有 5 条记录（id: 41, 42, 43, 44, 45），请说明要操作哪一条。",
      options: [
        { label: "id=41 · 陈思远", value: "我要操作 id=41 的这条记录" },
        { label: "id=42 · 林小满", value: "我要操作 id=42 的这条记录" },
        { label: "id=43 · 赵一鸣", value: "我要操作 id=43 的这条记录" },
        { label: "id=44 · 周雨桐", value: "我要操作 id=44 的这条记录" },
      ],
    }),

    message("user", "text", "我要操作 id=43 的这条记录"),

    message("assistant", "confirm", {
      summary: "将删除「报名记录」 1 条（id: 43，报名人：赵一鸣，状态：已取消），令牌 5 分钟内有效。",
    }),

    message("user", "text", "确认"),

    message("assistant", "text", "已删除 1 条。删除会记入审计日志；如需恢复请联系管理员从备份找回。"),

    message("user", "text", "做一个图书借阅管理系统。"),

    message("assistant", "ask", {
      kind: "clarify",
      text: `开始建模前有几个问题（直接回复答案即可，或回复「按默认」跳过）：
1. 借阅期限默认多少天？（默认：30 天）
2. 逾期按什么标准计费？（默认：每天 0.5 元）`,
      items: [
        { question: "借阅期限默认多少天？", default: "30 天" },
        { question: "逾期按什么标准计费？", default: "每天 0.5 元" },
      ],
      options: [{ label: "全部按默认", value: "按默认" }],
    }),

    message("user", "text", "按默认"),

    message("assistant", "plan", {
      summary: PLAN_SUMMARY,
      version: "",
      assumptions: [
        "借阅期限按 30 天计算，超期按每天 0.5 元计费（可后续调整）",
        "图书库存按「可借册数」维护，借出与归还各扣减/回补一本",
        "读者状态默认「正常」，欠费或超期未还时由管理员手工停用",
      ],
    }),

    message("assistant", "phase", null),

    message("assistant", "text", `新系统已上线：**图书借阅管理系统 v3**。

- 3 个实体、15 个接口，编译与自动验收全部通过
- 首个管理员账号已随版本创建，可到产物侧登录
- 想继续改，直接说「给图书加一个 ISBN 字段」，会走同一套确认流程`),
  ];

  // 生成过程面板：一次已经跑完的生成，用来展示阶段条 / 产物文件 / 日志三块
  events.mode = "generation";
  events.running = false;
  events.failed = false;
  events.steps = [
    { step: "解析需求", ok: true, detail: "识别 3 个实体、17 个字段" },
    { step: "规划模型", ok: true, detail: "生成版本 v3 的元数据" },
    { step: "渲染代码", ok: true, detail: "38 个文件" },
    { step: "编译", ok: true, detail: "maven package 通过（28s）" },
    { step: "启动产物", ok: true, detail: "端口 8081，健康检查通过" },
    { step: "自动验收", ok: true, detail: "62 条用例全绿" },
  ];
  events.files = [
    "src/main/java/com/generated/generated/controller/BookController.java",
    "src/main/java/com/generated/generated/controller/ReaderController.java",
    "src/main/java/com/generated/generated/controller/LoanController.java",
    "src/main/java/com/generated/generated/service/BookService.java",
    "src/main/java/com/generated/generated/service/LoanService.java",
    "src/main/resources/db/migration/V3__loan.sql",
    "src/main/resources/application.yml",
    "src/test/java/com/generated/generated/LoanApiTest.java",
    "pom.xml",
    "README.md",
  ];
  events.logs = [
    "[maven] Compiling 38 source files to target/classes",
    "[maven] BUILD SUCCESS in 28.412 s",
    "[runtime] starting generated app on :8081",
    "[runtime] Actuator health = UP",
    "[acceptance] 62/62 cases passed",
  ];
}

export { DEMO_REPLY };
