import { createRouter, createWebHistory } from "vue-router";
import ImportData from "../views/ImportData.vue";
import Eval from "../views/Eval.vue";
import Home from "../views/Home.vue";
import ModelManagement from "../views/ModelManagement.vue";
import Verify from "../views/Verify.vue";
import Merge from "../views/Merge.vue";
import Train from "../views/Train.vue";
import SystemInfo from "../views/SystemInfo.vue";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: Home, meta: { title: "设置" } },
    { path: "/importData", name: "importData", component: ImportData, meta: { title: "数据导入与数据集" } },
    { path: "/datasets", redirect: { name: "importData" } },
    { path: "/models", name: "models", component: ModelManagement, meta: { title: "模型管理" } },
    { path: "/train", name: "train", component: Train, meta: { title: "训练" } },
    { path: "/verify", name: "playground", component: Verify, meta: { title: "推理沙盒" } },
    { path: "/merge", name: "merge", component: Merge, meta: { title: "LoRA 合并" } },
    { path: "/eval", name: "eval", component: Eval, meta: { title: "评测" } },
    { path: "/system-info", name: "systemInfo", component: SystemInfo, meta: { title: "系统信息" } },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "数据工坊")} · 数据工坊`;
});

export default router;
