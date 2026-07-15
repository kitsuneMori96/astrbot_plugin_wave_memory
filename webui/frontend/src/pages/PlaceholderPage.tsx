import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">该页面的数据视图会在后续任务接入现有 WebUI API。</p>
      </CardContent>
    </Card>
  )
}
