import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const useNotificationsMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/useNotifications', () => ({
  useNotifications: useNotificationsMock,
}))

import {
  NotificationProvider,
  useNotificationController,
} from '@/components/alerts/NotificationProvider'

describe('NotificationProvider', () => {
  it('shares one controller instance with every consumer', () => {
    const controller = { ready: true, subscribed: false }
    const consume = vi.fn()
    useNotificationsMock.mockReturnValue(controller)

    function Consumer() {
      consume(useNotificationController())
      return null
    }

    render(
      <NotificationProvider>
        <Consumer />
        <Consumer />
      </NotificationProvider>,
    )

    expect(useNotificationsMock).toHaveBeenCalledOnce()
    expect(consume).toHaveBeenCalledTimes(2)
    expect(consume.mock.calls.every(([value]) => value === controller)).toBe(true)
  })
})
