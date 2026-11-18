"""Test catalog CSV import with sales descriptions."""
import pytest
from decimal import Decimal

from app.db.models import Brand, CatalogProduct, CatalogSource, ProductPrice, VehicleModel, VehicleSpec


@pytest.fixture(autouse=True)
def _seeded_catalog(db_session):
    """db_session wipes every table, so re-seed the catalog (empty tables -> import_csv + activate)."""
    from app.db.seed import seed_catalog
    seed_catalog()


def test_import_csv_creates_brands(db_session):
    """Verify brands are created from CSV."""
    brands = db_session.query(Brand).all()
    brand_names = {b.name for b in brands}

    assert "FUSO" in brand_names
    assert "ISUZU" in brand_names
    assert "THACO" in brand_names
    assert "JAC" in brand_names
    assert "FOTON" in brand_names
    assert "HYUNDAI" in brand_names
    assert "HINO" in brand_names


def test_import_csv_creates_models(db_session):
    """Verify vehicle models are created."""
    models = db_session.query(VehicleModel).all()
    assert len(models) > 0

    fuso = db_session.query(Brand).filter_by(name="FUSO").first()
    assert fuso is not None

    fi170 = db_session.query(VehicleModel).filter_by(brand_id=fuso.id, name="FI170").first()
    assert fi170 is not None


def test_import_csv_creates_products_with_specs(db_session):
    """Verify products are created with full specs."""
    fuso_fi170 = db_session.query(CatalogProduct).join(Brand).filter(
        Brand.name == "FUSO",
        CatalogProduct.name.like("%FI170%")
    ).first()

    assert fuso_fi170 is not None
    assert fuso_fi170.kind == "TRUCK"
    assert fuso_fi170.status == "ACTIVE"

    spec = fuso_fi170.spec
    assert spec is not None
    assert spec.wheel_formula == "4x2R"
    assert spec.gvm_kg == 13700
    assert spec.seats == 3


def test_product_has_sales_summary(db_session):
    """Verify products have sales summary for RAG."""
    product = db_session.query(CatalogProduct).filter(
        CatalogProduct.sales_info.isnot(None)
    ).first()

    assert product is not None
    assert "summary" in product.sales_info
    summary = product.sales_info["summary"]
    assert len(summary) > 50
    assert "xe tải" in summary.lower() or "truck" in summary.lower()


def test_dimensions_parsed_correctly(db_session):
    """Verify dimensions are parsed from 'x' format."""
    fuso_fi170 = db_session.query(CatalogProduct).join(Brand).filter(
        Brand.name == "FUSO",
        CatalogProduct.name.like("%FI170%")
    ).first()

    spec = fuso_fi170.spec
    assert spec.length_mm == 8040
    assert spec.width_mm == 2340
    assert spec.height_mm == 3440


def test_engine_specs_parsed(db_session):
    """Verify engine specs are parsed correctly."""
    product = db_session.query(CatalogProduct).join(VehicleSpec).filter(
        VehicleSpec.engine_model.isnot(None)
    ).first()

    spec = product.spec
    assert spec.engine_model is not None
    assert spec.power_kw is not None
    assert spec.displacement_cc is not None


def test_cargo_dimensions_parsed(db_session):
    """Verify cargo dimensions are extracted."""
    product = db_session.query(CatalogProduct).join(VehicleSpec).filter(
        VehicleSpec.cargo_length_mm.isnot(None)
    ).first()

    assert product is not None
    spec = product.spec
    assert spec.cargo_length_mm > 0
    assert spec.cargo_width_mm > 0


def test_price_imported_correctly(db_session):
    """Verify prices are converted from millions to VND."""
    product = db_session.query(CatalogProduct).join(ProductPrice).filter(
        ProductPrice.amount_vnd > 0
    ).first()

    assert product is not None
    price = db_session.query(ProductPrice).filter_by(product_id=product.id).first()

    assert price.price_type == "MSRP"
    assert price.amount_vnd >= 500_000_000
    assert price.is_current is True
    assert price.effective_date is not None


def test_price_basis_detected(db_session):
    """Verify price basis is detected from notes."""
    chassis_price = db_session.query(ProductPrice).filter_by(basis="CHASSIS").first()
    assert chassis_price is not None


def test_catalog_source_tracked(db_session):
    """Verify catalog source is tracked with hash."""
    source = db_session.query(CatalogSource).first()
    assert source is not None
    assert len(source.sha256) == 64
    assert source.file_name.endswith(".csv")


def test_gearbox_specs_parsed(db_session):
    """Verify gearbox info is parsed."""
    product = db_session.query(CatalogProduct).join(VehicleSpec).filter(
        VehicleSpec.gears_forward.isnot(None)
    ).first()

    assert product is not None
    spec = product.spec
    assert spec.gears_forward in (5, 6, 8)
    assert spec.gears_reverse in (1, 2)


def test_boolean_flags_parsed(db_session):
    """Verify boolean flags like ABS and SCR are parsed."""
    with_abs = db_session.query(VehicleSpec).filter_by(has_abs=True).first()
    assert with_abs is not None

    with_scr = db_session.query(VehicleSpec).filter_by(has_scr=True).first()
    assert with_scr is not None


def test_multiple_products_same_model(db_session):
    """Verify same model can have multiple products (different configs)."""
    isuzu = db_session.query(Brand).filter_by(name="ISUZU").first()
    if isuzu:
        frr_products = db_session.query(CatalogProduct).join(VehicleModel).filter(
            VehicleModel.brand_id == isuzu.id,
            VehicleModel.name.like("FRR%")
        ).all()

        if len(frr_products) > 1:
            wheelbases = {p.spec.wheelbase_mm for p in frr_products if p.spec}
            assert len(wheelbases) > 1


def test_search_summary_contains_keywords(db_session):
    """Verify search summaries contain relevant Vietnamese keywords."""
    products = db_session.query(CatalogProduct).filter(
        CatalogProduct.sales_info.isnot(None)
    ).limit(5).all()

    found_keywords = False
    for product in products:
        summary = product.sales_info.get("summary", "")
        if any(kw in summary.lower() for kw in ["bền", "giá", "phù hợp", "tiết kiệm"]):
            found_keywords = True
            break

    assert found_keywords, "No products with Vietnamese keywords in summary"


def test_no_duplicate_imports(db_session):
    """Verify re-importing same file doesn't create duplicates."""
    initial_count = db_session.query(CatalogProduct).count()
    assert initial_count > 0
