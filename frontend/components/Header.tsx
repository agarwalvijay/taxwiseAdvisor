'use client'
import { UserButton, useUser } from '@clerk/nextjs'
import Link from 'next/link'

export default function Header() {
  const { user } = useUser()
  return (
    <header className="bg-[#1F4E79] text-white px-6 py-4 flex items-center justify-between shadow-md">
      <Link href="/clients" className="text-xl font-bold tracking-tight hover:text-blue-200 transition-colors">
        TaxWise Advisor
      </Link>
      <div className="flex items-center gap-4">
        {user && (
          <span className="text-sm text-blue-200">
            {user.fullName ?? user.emailAddresses[0]?.emailAddress}
          </span>
        )}
        <UserButton />
      </div>
    </header>
  )
}
