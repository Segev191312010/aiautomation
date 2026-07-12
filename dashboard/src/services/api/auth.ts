import type { User } from '@/types'
import { get } from './client'

export const fetchAuthMe    = () => get<User>('/api/auth/me')
