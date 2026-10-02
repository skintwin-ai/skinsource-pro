from flask import Blueprint, request, jsonify
from src.chain_commands import (
    offering_qualification,
    record_supplier_qualification,
    use_shared_ledger,
)
from src.models.user import db
from src.models import Supplier, SupplierIngredient, Ingredient
from sqlalchemy import or_, and_, desc

suppliers_bp = Blueprint('suppliers', __name__)

@suppliers_bp.route('/suppliers', methods=['GET'])
def get_suppliers():
    """Get all suppliers with optional filtering"""
    try:
        # Get query parameters
        country = request.args.get('country')
        verified_only = request.args.get('verified', type=bool)
        min_score = request.args.get('min_score', type=float)
        search = request.args.get('search')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        sort_by = request.args.get('sort_by', 'overall_score')  # overall_score, company_name, created_at
        
        # Build query
        query = Supplier.query.filter(Supplier.active_status == True)
        
        # Apply filters
        if country:
            query = query.filter(Supplier.country == country)
        
        if verified_only:
            query = query.filter(Supplier.verified_status == True)
            
        if min_score:
            query = query.filter(Supplier.overall_score >= min_score)
            
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Supplier.company_name.ilike(search_term),
                    Supplier.address.ilike(search_term),
                    Supplier.country.ilike(search_term)
                )
            )
        
        # Apply sorting
        if sort_by == 'overall_score':
            query = query.order_by(desc(Supplier.overall_score))
        elif sort_by == 'company_name':
            query = query.order_by(Supplier.company_name)
        elif sort_by == 'created_at':
            query = query.order_by(desc(Supplier.created_at))
        
        # Paginate results
        pagination = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        suppliers = [supplier.to_dict() for supplier in pagination.items]
        
        return jsonify({
            'suppliers': suppliers,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_next': pagination.has_next,
                'has_prev': pagination.has_prev
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/<int:supplier_id>', methods=['GET'])
def get_supplier(supplier_id):
    """Get a specific supplier by ID"""
    try:
        supplier = Supplier.query.get_or_404(supplier_id)
        
        # Get ingredients offered by this supplier
        supplier_ingredients = SupplierIngredient.query.filter_by(
            supplier_id=supplier_id
        ).join(Ingredient).all()
        
        ingredients = []
        for si in supplier_ingredients:
            ingredient_data = si.ingredient.to_dict()
            ingredient_data['offering'] = si.to_dict()
            ingredients.append(ingredient_data)
        
        supplier_data = supplier.to_dict()
        supplier_data['ingredients'] = ingredients
        
        return jsonify(supplier_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers', methods=['POST'])
def create_supplier():
    """Create a new supplier"""
    try:
        data = request.get_json()
        
        if not data or 'company_name' not in data:
            return jsonify({'error': 'Company name is required'}), 400
        
        supplier = Supplier(
            company_name=data['company_name'],
            contact_email=data.get('contact_email'),
            contact_phone=data.get('contact_phone'),
            website=data.get('website'),
            address=data.get('address'),
            country=data.get('country'),
            quality_score=data.get('scores', {}).get('quality', 0.0),
            reliability_score=data.get('scores', {}).get('reliability', 0.0),
            sustainability_score=data.get('scores', {}).get('sustainability', 0.0),
            price_competitiveness_score=data.get('scores', {}).get('price_competitiveness', 0.0),
            verified_status=data.get('verified_status', False)
        )
        
        # Set JSON fields
        if 'contact_info' in data:
            supplier.set_contact_info(data['contact_info'])
        
        if 'certifications' in data:
            supplier.set_certifications(data['certifications'])
        
        if 'geographic_regions' in data:
            supplier.geographic_regions = json.dumps(data['geographic_regions'])
        
        if 'specialties' in data:
            supplier.specialties = json.dumps(data['specialties'])
        
        # Calculate overall score
        supplier.calculate_overall_score()
        
        db.session.add(supplier)
        db.session.commit()
        
        return jsonify(supplier.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/<int:supplier_id>', methods=['PUT'])
def update_supplier(supplier_id):
    """Update an existing supplier"""
    try:
        supplier = Supplier.query.get_or_404(supplier_id)
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update basic fields
        for field in ['company_name', 'contact_email', 'contact_phone', 'website', 
                     'address', 'country', 'verified_status', 'active_status']:
            if field in data:
                setattr(supplier, field, data[field])
        
        # Update scores
        if 'scores' in data:
            scores = data['scores']
            supplier.quality_score = scores.get('quality', supplier.quality_score)
            supplier.reliability_score = scores.get('reliability', supplier.reliability_score)
            supplier.sustainability_score = scores.get('sustainability', supplier.sustainability_score)
            supplier.price_competitiveness_score = scores.get('price_competitiveness', supplier.price_competitiveness_score)
            supplier.calculate_overall_score()
        
        # Update JSON fields
        if 'contact_info' in data:
            supplier.set_contact_info(data['contact_info'])
        
        if 'certifications' in data:
            supplier.set_certifications(data['certifications'])
        
        if 'geographic_regions' in data:
            import json
            supplier.geographic_regions = json.dumps(data['geographic_regions'])
        
        if 'specialties' in data:
            import json
            supplier.specialties = json.dumps(data['specialties'])
        
        db.session.commit()
        
        return jsonify(supplier.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/<int:supplier_id>', methods=['DELETE'])
def delete_supplier(supplier_id):
    """Delete a supplier (soft delete by setting active_status to False)"""
    try:
        supplier = Supplier.query.get_or_404(supplier_id)
        supplier.active_status = False
        db.session.commit()
        
        return jsonify({'message': 'Supplier deactivated successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/<int:supplier_id>/ingredients', methods=['GET'])
def get_supplier_ingredients(supplier_id):
    """Get all ingredients offered by a specific supplier"""
    try:
        supplier = Supplier.query.get_or_404(supplier_id)
        
        supplier_ingredients = SupplierIngredient.query.filter_by(
            supplier_id=supplier_id
        ).join(Ingredient).all()
        
        ingredients = []
        for si in supplier_ingredients:
            ingredient_data = si.ingredient.to_dict()
            ingredient_data['offering'] = si.to_dict()
            ingredients.append(ingredient_data)
        
        return jsonify({
            'supplier': supplier.to_dict(),
            'ingredients': ingredients,
            'total': len(ingredients)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/<int:supplier_id>/ingredients', methods=['POST'])
def add_supplier_ingredient(supplier_id):
    """Add an ingredient offering for a supplier"""
    try:
        supplier = Supplier.query.get_or_404(supplier_id)
        data = request.get_json()
        
        if not data or 'ingredient_id' not in data:
            return jsonify({'error': 'Ingredient ID is required'}), 400
        
        # Check if ingredient exists
        ingredient = Ingredient.query.get_or_404(data['ingredient_id'])
        
        # Check if relationship already exists
        existing = SupplierIngredient.query.filter_by(
            supplier_id=supplier_id,
            ingredient_id=data['ingredient_id']
        ).first()
        
        if existing:
            return jsonify({'error': 'Supplier already offers this ingredient'}), 400

        use_shared_ledger()
        qualified, status = record_supplier_qualification(
            offering_qualification(data, supplier.company_name, ingredient.name)
        )
        if status != 200:
            return jsonify({"error": qualified.get("error", "qualification rejected")}), status

        supplier_ingredient = SupplierIngredient(
            supplier_id=supplier_id,
            ingredient_id=data['ingredient_id'],
            price_per_kg=data.get('price_per_kg'),
            minimum_order_quantity=data.get('minimum_order_quantity'),
            lead_time_days=data.get('lead_time_days'),
            availability_status=data.get('availability_status', 'available'),
            grade=data.get('grade'),
            purity_percentage=data.get('purity_percentage'),
            storage_conditions=data.get('storage_conditions'),
            shelf_life_months=data.get('shelf_life_months')
        )
        
        if 'packaging_options' in data:
            supplier_ingredient.set_packaging_options(data['packaging_options'])
        
        db.session.add(supplier_ingredient)
        db.session.commit()
        
        return jsonify(supplier_ingredient.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@suppliers_bp.route('/suppliers/search', methods=['POST'])
def search_suppliers():
    """Advanced supplier search"""
    try:
        data = request.get_json()
        query_text = data.get('query', '')
        filters = data.get('filters', {})
        ingredient_id = data.get('ingredient_id')
        
        # Start with base query
        query = Supplier.query.filter(Supplier.active_status == True)
        
        # Filter by ingredient if specified
        if ingredient_id:
            query = query.join(SupplierIngredient).filter(
                SupplierIngredient.ingredient_id == ingredient_id
            )
        
        # Apply text search
        if query_text:
            search_term = f"%{query_text}%"
            query = query.filter(
                or_(
                    Supplier.company_name.ilike(search_term),
                    Supplier.country.ilike(search_term),
                    Supplier.address.ilike(search_term)
                )
            )
        
        # Apply filters
        if filters.get('countries'):
            query = query.filter(Supplier.country.in_(filters['countries']))
            
        if filters.get('verified_only'):
            query = query.filter(Supplier.verified_status == True)
            
        if filters.get('min_overall_score'):
            query = query.filter(Supplier.overall_score >= filters['min_overall_score'])
        
        # Order by overall score
        query = query.order_by(desc(Supplier.overall_score))
        
        # Execute query
        suppliers = query.limit(50).all()
        
        return jsonify({
            'suppliers': [supplier.to_dict() for supplier in suppliers],
            'total': len(suppliers)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

