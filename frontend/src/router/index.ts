import { createRouter, createWebHistory } from "vue-router";
import DataImport from "../views/DataImport.vue";
import Inference from "../views/Inference.vue";
import Training from "../views/Training.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/data" },
    { path: "/data", name: "data", component: DataImport, meta: { title: "数据导入" } },
    { path: "/training", name: "training", component: Training, meta: { title: "训练" } },
    { path: "/inference", name: "inference", component: Inference, meta: { title: "推理沙盒" } },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "数据工坊")} · 数据工坊`;
});

export default router;
