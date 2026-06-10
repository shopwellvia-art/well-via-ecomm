import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  AlertTriangle,
  ImageOff,
  Percent,
  DollarSign,
  Package,
  Tag,
  Boxes,
  Scale,
  ShieldOff,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { ProductImageManager } from '@/components/admin/ProductImageManager.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { useProduct } from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { useCreateProduct, useUpdateProduct } from '@/features/admin/hooks.js';
import { useTaxes, useSetProductTaxes } from '@/features/taxes/hooks.js';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

const EMPTY = {
  sku: '',
  name: '',
  description: '',
  price: '',
  compare_at_price: '',
  cost: '',
  stock: '',
  weight_grams: '',
  cod_blocked: false,
  category_id: '',
};

/** Thin section heading used inside form cards */
function SectionLabel({ icon: Icon, children }) {
  return (
    <p className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
      {Icon && <Icon className="size-3.5 shrink-0" aria-hidden="true" />}
      {children}
    </p>
  );
}

export default function AdminProductFormPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();

  const { data: product, isLoading, isError } = useProduct(isEdit ? id : undefined);
  const { data: categories = [] } = useCategories();
  const { data: taxes = [] } = useTaxes();
  const createProduct = useCreateProduct();
  const updateProduct = useUpdateProduct();
  const setProductTaxes = useSetProductTaxes();

  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState(null);
  const [selectedTaxIds, setSelectedTaxIds] = useState([]);

  const activeTaxes = useMemo(() => taxes.filter((t) => t.is_active), [taxes]);

  useEffect(() => {
    if (isEdit && product) {
      setForm({
        sku: product.sku ?? '',
        name: product.name ?? '',
        description: product.description ?? '',
        price: String(product.price ?? ''),
        compare_at_price:
          product.compare_at_price != null ? String(product.compare_at_price) : '',
        cost: product.cost != null ? String(product.cost) : '',
        stock: String(product.stock ?? ''),
        weight_grams:
          product.weight_grams != null ? String(product.weight_grams) : '',
        cod_blocked: !!product.cod_blocked,
        category_id: product.category_id != null ? String(product.category_id) : '',
      });
      setSelectedTaxIds((product.taxes || []).map((t) => t.id));
    }
  }, [isEdit, product]);

  function toggleTax(taxId) {
    setSelectedTaxIds((cur) =>
      cur.includes(taxId) ? cur.filter((x) => x !== taxId) : [...cur, taxId],
    );
  }

  const initialTaxIds = useMemo(
    () => new Set((product?.taxes || []).map((t) => t.id)),
    [product],
  );
  const taxesChanged = useMemo(() => {
    if (selectedTaxIds.length !== initialTaxIds.size) return true;
    return selectedTaxIds.some((taxId) => !initialTaxIds.has(taxId));
  }, [selectedTaxIds, initialTaxIds]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  function validate() {
    const next = {};
    if (!form.name.trim()) next.name = 'Name is required.';
    if (!isEdit && !form.sku.trim()) next.sku = 'SKU is required.';
    if (form.price === '' || Number.isNaN(Number(form.price)) || Number(form.price) < 0) {
      next.price = 'Enter a price of 0 or more.';
    }
    if (form.compare_at_price !== '') {
      const compare = Number(form.compare_at_price);
      if (Number.isNaN(compare) || compare < 0) {
        next.compare_at_price = 'Enter an amount of 0 or more.';
      } else if (Number(form.price) >= 0 && compare <= Number(form.price)) {
        next.compare_at_price = 'Must be greater than the price.';
      }
    }
    if (form.cost !== '') {
      const cost = Number(form.cost);
      if (Number.isNaN(cost) || cost < 0) {
        next.cost = 'Enter a cost of 0 or more.';
      }
    }
    if (form.stock !== '' && (Number.isNaN(Number(form.stock)) || Number(form.stock) < 0)) {
      next.stock = 'Stock cannot be negative.';
    }
    if (form.weight_grams !== '') {
      const w = Number(form.weight_grams);
      if (Number.isNaN(w) || w < 0 || w > 200_000) {
        next.weight_grams = 'Enter a weight between 0 and 200000 grams.';
      }
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function buildPayload() {
    const base = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      price: Number(form.price),
      compare_at_price:
        form.compare_at_price === '' ? null : Number(form.compare_at_price),
      cost: form.cost === '' ? null : Number(form.cost),
      stock: form.stock === '' ? 0 : Number(form.stock),
      weight_grams:
        form.weight_grams === '' ? null : Number(form.weight_grams),
      cod_blocked: !!form.cod_blocked,
      category_id: form.category_id === '' ? null : Number(form.category_id),
    };
    return isEdit ? base : { sku: form.sku.trim(), ...base };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setServerError(null);
    if (!validate()) return;
    try {
      if (isEdit) {
        await updateProduct.mutateAsync({ id, data: buildPayload() });
        if (taxesChanged) {
          await setProductTaxes.mutateAsync({ productId: id, taxIds: selectedTaxIds });
        }
      } else {
        const created = await createProduct.mutateAsync(buildPayload());
        if (selectedTaxIds.length > 0 && created?.id != null) {
          await setProductTaxes.mutateAsync({
            productId: created.id,
            taxIds: selectedTaxIds,
          });
        }
      }
      navigate('/admin/products');
    } catch (err) {
      setServerError(
        err.response?.data?.error?.message || 'Could not save the product. Try again.',
      );
    }
  }

  const busy =
    createProduct.isPending || updateProduct.isPending || setProductTaxes.isPending;

  if (isEdit && isLoading) {
    return (
      <AdminPage title="Edit product">
        <div className="max-w-2xl space-y-4">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-48 rounded-lg" />
          <Skeleton className="h-40 rounded-lg" />
          <Skeleton className="h-28 rounded-lg" />
        </div>
      </AdminPage>
    );
  }

  if (isEdit && (isError || !product)) {
    return (
      <AdminPage title="Edit product">
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="Product not found"
          description="This product may have been removed."
          action={
            <Link to="/admin/products">
              <Button size="sm">Back to products</Button>
            </Link>
          }
        />
      </AdminPage>
    );
  }

  return (
    <AdminPage
      title={isEdit ? 'Edit product' : 'New product'}
      description={
        isEdit ? 'Update the details of this product.' : 'Add a product to the catalog.'
      }
    >
      <Link
        to="/admin/products"
        className="mb-6 inline-flex items-center gap-1.5 rounded-sm text-sm text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to products
      </Link>

      <motion.div
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
        className="max-w-2xl space-y-5"
      >
        <form onSubmit={handleSubmit} noValidate>
          {/* ── Identity ── */}
          <motion.div variants={fadeUp}>
            <Card className="p-5 shadow-md">
              <SectionLabel icon={Tag}>Identity</SectionLabel>
              <div className="space-y-4">
                <Input
                  label="SKU"
                  value={form.sku}
                  onChange={set('sku')}
                  error={errors.sku}
                  required={!isEdit}
                  disabled={isEdit}
                  helper={isEdit ? 'SKU cannot be changed after creation.' : 'Unique product code.'}
                  placeholder="AUD-AURA-01"
                />
                <Input
                  label="Name"
                  value={form.name}
                  onChange={set('name')}
                  error={errors.name}
                  required
                  placeholder="Aura Wireless Headphones"
                />
                <Textarea
                  label="Description"
                  value={form.description}
                  onChange={set('description')}
                  placeholder="A short, appealing product description."
                  maxRows={8}
                />
              </div>
            </Card>
          </motion.div>

          {/* ── Pricing ── */}
          <motion.div variants={fadeUp} className="mt-5">
            <Card className="p-5 shadow-md">
              <SectionLabel icon={DollarSign}>Pricing</SectionLabel>
              <div className="grid gap-4 sm:grid-cols-3">
                <Input
                  label="Price (INR)"
                  type="number"
                  step="0.01"
                  min="0"
                  value={form.price}
                  onChange={set('price')}
                  error={errors.price}
                  required
                  placeholder="299.00"
                />
                <Input
                  label="Compare-at price"
                  type="number"
                  step="0.01"
                  min="0"
                  value={form.compare_at_price}
                  onChange={set('compare_at_price')}
                  error={errors.compare_at_price}
                  helper="Optional. Shows as the struck-through original next to a Sale badge."
                  placeholder="399.00"
                />
                <Input
                  label="Cost price (₹)"
                  type="number"
                  step="0.01"
                  min="0"
                  value={form.cost}
                  onChange={set('cost')}
                  error={errors.cost}
                  helper="Optional. Used for profitability analytics — not shown to customers."
                  placeholder="150.00"
                />
              </div>
            </Card>
          </motion.div>

          {/* ── Inventory & Shipping ── */}
          <motion.div variants={fadeUp} className="mt-5">
            <Card className="p-5 shadow-md">
              <SectionLabel icon={Boxes}>Inventory &amp; Shipping</SectionLabel>
              <div className="grid gap-4 sm:grid-cols-2">
                <Input
                  label="Stock"
                  type="number"
                  step="1"
                  min="0"
                  value={form.stock}
                  onChange={set('stock')}
                  error={errors.stock}
                  placeholder="24"
                />
                <Input
                  label="Weight (grams)"
                  type="number"
                  step="1"
                  min="0"
                  value={form.weight_grams}
                  onChange={set('weight_grams')}
                  error={errors.weight_grams}
                  helper="Optional. Drives shipping cost — leave blank to use the 200 g fallback."
                  placeholder="450"
                />
              </div>

              <div className="mt-4 flex items-start gap-3 rounded-md border border-line-subtle bg-bg-sunken px-4 py-3">
                <ShieldOff className="mt-0.5 size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                <label className="flex flex-1 cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={form.cod_blocked}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, cod_blocked: e.target.checked }))
                    }
                    className="mt-0.5 size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent focus-visible:focus-ring"
                  />
                  <span className="flex-1 text-sm">
                    <span className="block font-medium text-ink-primary">
                      Block Cash on Delivery
                    </span>
                    <span className="block text-xs text-ink-tertiary">
                      When checked, any cart containing this product disables COD at
                      checkout. Use for fragile or high-value items.
                    </span>
                  </span>
                </label>
              </div>
            </Card>
          </motion.div>

          {/* ── Organisation ── */}
          <motion.div variants={fadeUp} className="mt-5">
            <Card className="p-5 shadow-md">
              <SectionLabel icon={Package}>Organisation</SectionLabel>
              <Select
                label="Category"
                value={form.category_id}
                onChange={set('category_id')}
                helper="Optional — used for browsing and filtering."
              >
                <option value="">Uncategorized</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Card>
          </motion.div>

          {/* ── Taxes ── */}
          <motion.div variants={fadeUp} className="mt-5">
            <Card className="shadow-md">
              <CardHeader
                title={
                  <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    <Percent className="size-3.5" aria-hidden="true" />
                    Taxes
                    <span className="font-normal normal-case tracking-normal text-ink-tertiary">
                      ({selectedTaxIds.length} selected)
                    </span>
                  </span>
                }
              />
              <div className="p-5">
                {activeTaxes.length === 0 ? (
                  <p className="text-xs text-ink-tertiary">
                    No active taxes yet — create one on{' '}
                    <Link to="/admin/taxes" className="text-accent underline hover:text-ink-primary">
                      /admin/taxes
                    </Link>{' '}
                    first.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {activeTaxes.map((t) => {
                      const checked = selectedTaxIds.includes(t.id);
                      return (
                        <label
                          key={t.id}
                          className={cn(
                            'flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                            checked
                              ? 'bg-accent/10 text-ink-primary'
                              : 'text-ink-secondary hover:bg-fill',
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleTax(t.id)}
                            className="size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent focus-visible:focus-ring"
                          />
                          <span className="flex-1">{t.name}</span>
                          <span className="nums font-mono text-xs text-ink-tertiary">
                            {Number(t.rate).toFixed(3)}%
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            </Card>
          </motion.div>

          {/* ── Server error ── */}
          {serverError && (
            <motion.p
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-4 flex items-start gap-2 rounded-md bg-danger/10 px-4 py-3 text-sm text-danger shadow-glow-danger"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {serverError}
            </motion.p>
          )}

          {/* ── Actions ── */}
          <motion.div variants={fadeUp} className="mt-6 flex gap-3">
            <Button type="submit" loading={busy}>
              {isEdit ? 'Save changes' : 'Create product'}
            </Button>
            <Link to="/admin/products">
              <Button type="button" variant="outline" disabled={busy}>
                Cancel
              </Button>
            </Link>
          </motion.div>
        </form>

        {/* ── Images ── */}
        <motion.div variants={fadeUp}>
          <Card className="shadow-md">
            <CardHeader title="Product images" />
            <div className="p-5">
              {isEdit ? (
                <ProductImageManager
                  key={product.id}
                  productId={product.id}
                  initialImages={product.images || []}
                />
              ) : (
                <div className="flex items-center gap-3 rounded-md border border-dashed border-line-strong bg-bg-sunken px-4 py-4 text-sm text-ink-secondary">
                  <ImageOff className="size-5 shrink-0 text-ink-tertiary" aria-hidden="true" />
                  <span>
                    Save the product first — then edit it to upload up to 8 images.
                  </span>
                </div>
              )}
            </div>
          </Card>
        </motion.div>
      </motion.div>
    </AdminPage>
  );
}
