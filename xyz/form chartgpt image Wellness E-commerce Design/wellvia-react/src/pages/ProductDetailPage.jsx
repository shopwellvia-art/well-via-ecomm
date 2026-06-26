import { useParams } from 'react-router-dom';
import { getProductBySlug } from '../data/products';
import ProductDetail from '../components/ProductDetail';
import Footer from '../components/Footer';

export default function ProductDetailPage() {
  const { slug } = useParams();
  const product = getProductBySlug(slug);

  return (
    <>
      <ProductDetail product={product} />
      <Footer />
    </>
  );
}
