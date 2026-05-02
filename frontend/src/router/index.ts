import { createRouter, createWebHistory } from "vue-router";
import DataSet from "../views/DataSet.vue";
import Home from "../views/Home.vue";
import ModelManagement from "../views/ModelManagement.vue";
import Train from "../views/Train.vue";
import SystemInfo from "../views/SystemInfo.vue";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: Home, meta: { title: "设置" } },
    { path: "/importData", name: "importData", component: DataSet, meta: { title: "数据导入与数据集" } },
    { path: "/datasets", redirect: { name: "importData" } },
    { path: "/models", name: "models", component: ModelManagement, meta: { title: "模型管理" } },
    { path: "/train", name: "train", component: Train, meta: { title: "训练" } },
    { path: "/system-info", name: "systemInfo", component: SystemInfo, meta: { title: "系统信息" } },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "数据工坊")} · 数据工坊`;
});

export default router;
