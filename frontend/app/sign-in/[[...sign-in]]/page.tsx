import { SignIn } from '@clerk/nextjs'

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-[#1F4E79] mb-8">TaxWise Advisor</h1>
        <SignIn />
      </div>
    </div>
  )
}
