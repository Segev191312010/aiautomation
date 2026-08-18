import { createContext, useContext } from 'react'
import { useNotifications, type UseNotificationsResult } from '@/hooks/useNotifications'

const NotificationContext = createContext<UseNotificationsResult | null>(null)

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const controller = useNotifications()
  return (
    <NotificationContext.Provider value={controller}>
      {children}
    </NotificationContext.Provider>
  )
}

export function useNotificationController(): UseNotificationsResult {
  const controller = useContext(NotificationContext)
  if (!controller) {
    throw new Error('useNotificationController must be used within NotificationProvider')
  }
  return controller
}
