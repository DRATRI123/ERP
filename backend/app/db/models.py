"""Import every model module so ``Base.metadata`` is complete (used by Alembic and tests)."""

from app.db.base import Base
from app.modules.access import models as access_models  # noqa: F401
from app.modules.accounting import models as accounting_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.automation import models as automation_models  # noqa: F401
from app.modules.billing import models as billing_models  # noqa: F401
from app.modules.businesses import models as business_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.claims import models as claim_models  # noqa: F401
from app.modules.compliance import models as compliance_models  # noqa: F401
from app.modules.developer import models as developer_models  # noqa: F401
from app.modules.digest import models as digest_models  # noqa: F401
from app.modules.events import models as event_models  # noqa: F401
from app.modules.field import models as field_models  # noqa: F401
from app.modules.files import models as file_models  # noqa: F401
from app.modules.integrations import models as integration_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.manufacturing import models as manufacturing_models  # noqa: F401
from app.modules.money import models as money_models  # noqa: F401
from app.modules.numbering import models as numbering_models  # noqa: F401
from app.modules.parties import models as party_models  # noqa: F401
from app.modules.payroll import models as payroll_models  # noqa: F401
from app.modules.pharmacy import models as pharmacy_models  # noqa: F401
from app.modules.pricing import models as pricing_models  # noqa: F401
from app.modules.shop import models as shop_models  # noqa: F401
from app.modules.stockx import models as stockx_models  # noqa: F401
from app.modules.system import models as system_models  # noqa: F401
from app.modules.users import models as user_models  # noqa: F401

metadata = Base.metadata
