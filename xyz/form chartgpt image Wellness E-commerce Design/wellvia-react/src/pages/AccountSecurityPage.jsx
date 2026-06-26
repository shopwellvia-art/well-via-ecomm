import AccountLayout from '../components/AccountLayout';

const inputCls = 'w-full bg-bg border border-line rounded-xl px-[15px] py-[13px] text-[14px]';
const labelCls = 'text-[11px] tracking-[0.1em] uppercase text-muted mb-1.5';

export default function AccountSecurityPage() {
  return (
    <AccountLayout>
      <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5">Account &amp; Security</h1>
      <p className="text-[14px] text-muted m-0 mb-6 font-light">Manage your personal details and how you sign in.</p>

      <div className="bg-card border border-line rounded-xl2 p-6 mb-[18px]">
        <div className="font-serif text-[20px] mb-[18px]">Profile</div>
        <div className="grid sm:grid-cols-2 gap-3.5">
          <div><div className={labelCls}>Full Name</div><input defaultValue="Aditi Kapoor" className={inputCls} /></div>
          <div><div className={labelCls}>Phone</div><input defaultValue="+91 98•••• ••21" className={inputCls} /></div>
          <div className="sm:col-span-2"><div className={labelCls}>Email</div><input defaultValue="aditi.k@email.com" className={inputCls} /></div>
        </div>
        <button className="mt-[18px] bg-green text-white border-0 rounded-full px-7 py-3 text-[13.5px] cursor-pointer hover:bg-greenh">Save Changes</button>
      </div>

      <div className="bg-card border border-line rounded-xl2 p-6">
        <div className="font-serif text-[20px] mb-1.5">Security</div>
        <div className="flex flex-wrap gap-3.5 justify-between items-center py-4 border-b border-line">
          <div><div className="text-[14.5px]">Password</div><div className="text-[12.5px] text-muted">Last changed 3 months ago</div></div>
          <button className="bg-transparent border border-line rounded-full px-[22px] py-2.5 text-[13px] cursor-pointer hover:border-green">Change</button>
        </div>
        <div className="flex flex-wrap gap-3.5 justify-between items-center py-4">
          <div><div className="text-[14.5px]">Two-Factor Authentication</div><div className="text-[12.5px] text-muted">Add an extra layer of security</div></div>
          <div className="flex items-center gap-2 text-[12px] text-green">
            <span className="w-[38px] h-[22px] rounded-full bg-green relative inline-block">
              <span className="absolute top-0.5 right-0.5 w-[18px] h-[18px] rounded-full bg-white" />
            </span>
            On
          </div>
        </div>
      </div>
    </AccountLayout>
  );
}
