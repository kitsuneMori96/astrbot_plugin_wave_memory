import { Navigate } from 'react-router-dom'

/**
 * Agent 反馈已并入学习中心。
 *
 * 保留该导出仅避免旧的外部构建引用在升级期间崩溃；应用路由和导航不再注册
 * 旧路径不再注册，候选列表、审核和历史统一由 LearningCenterPage 提供。
 */
export function AgentFeedbackPage() {
  return <Navigate replace to="/learning-center" />
}
