import { Navigate } from 'react-router-dom'

/**
 * 学习对象候选审核已迁入通用学习中心。
 * 该兼容导出不注册独立前端路由，避免旧入口继续调用 learning_object_review。
 */
export function LearningObjectsPage() {
  return <Navigate replace to="/learning-center" />
}
