from flask import Blueprint, request, jsonify
from src.chain_commands import (
    ingredient_column_values,
    ledger_ingredient_id,
    record_created_ingredient,
    record_updated_ingredient,
    respond,
    use_shared_ledger,
)
from src.models.user import db
from src.models import Ingredient, SupplierIngredient, Supplier
from sqlalchemy import or_, and_

ingredients_bp = Blueprint('ingredients', __name__)

@ingredients_bp.route('/ingredients', methods=['GET'])
def get_ingredients():
    """Get all ingredients with optional filtering"""
    try:
        # Get query parameters
        category = request.args.get('category')
        function = request.args.get('function')
        evidence_level = request.args.get('evidence_level')
        min_sustainability = request.args.get('min_sustainability', type=float)
        max_price = request.args.get('max_price', type=float)
        search = request.args.get('search')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # Build query
        query = Ingredient.query
        
        # Apply filters
        if category:
            query = query.filter(Ingredient.category == category)
        
        if function:
            query = query.filter(Ingredient.function.contains(function))
            
        if evidence_level:
            query = query.filter(Ingredient.evidence_level == evidence_level)
            
        if min_sustainability:
            query = query.filter(Ingredient.sustainability_score >= min_sustainability)
            
        if max_price:
            query = query.filter(Ingredient.price_range_max <= max_price)
            
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Ingredient.name.ilike(search_term),
                    Ingredient.inci_name.ilike(search_term),
                    Ingredient.description.ilike(search_term),
                    Ingredient.function.ilike(search_term)
                )
            )
        
        # Paginate results
        pagination = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        ingredients = [ingredient.to_dict() for ingredient in pagination.items]
        
        return jsonify({
            'ingredients': ingredients,
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

@ingredients_bp.route('/ingredients/<int:ingredient_id>', methods=['GET'])
def get_ingredient(ingredient_id):
    """Get a specific ingredient by ID"""
    try:
        ingredient = Ingredient.query.get_or_404(ingredient_id)
        
        # Get suppliers for this ingredient
        supplier_ingredients = SupplierIngredient.query.filter_by(
            ingredient_id=ingredient_id
        ).join(Supplier).all()
        
        suppliers = []
        for si in supplier_ingredients:
            supplier_data = si.supplier.to_dict()
            supplier_data['offering'] = si.to_dict()
            suppliers.append(supplier_data)
        
        ingredient_data = ingredient.to_dict()
        ingredient_data['suppliers'] = suppliers
        
        return jsonify(ingredient_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@ingredients_bp.route('/supply-chain', methods=['POST'])
def supply_chain_command():
    """Accept an ingredient, qualification, or lot command for the org ledger."""
    use_shared_ledger()
    body, status = respond(request.get_json(silent=True) or {})
    return jsonify(body), status


@ingredients_bp.route('/ingredients', methods=['POST'])
def create_ingredient():
    """Create a new ingredient"""
    try:
        data = request.get_json()
        
        if not data or 'name' not in data:
            return jsonify({'error': 'Name is required'}), 400

        use_shared_ledger()
        recorded = record_created_ingredient(data)
        if recorded is not None:
            body, status = recorded
            if status != 200:
                return jsonify({'error': body.get('error', 'ingredient rejected')}), status
        
        columns = ingredient_column_values(data)
        ingredient = Ingredient(
            name=data['name'],
            inci_name=columns.get('inci_name'),
            cas_number=columns.get('cas_number'),
            category=data.get('category', 'other'),
            function=data.get('function'),
            description=data.get('description'),
            sustainability_score=data.get('sustainability_score'),
            price_range_min=data.get('price_range', {}).get('min'),
            price_range_max=data.get('price_range', {}).get('max'),
            evidence_level=data.get('evidence_level')
        )
        
        # Set regulatory status if provided
        if 'regulatory_status' in data:
            ingredient.set_regulatory_status(data['regulatory_status'])
        
        db.session.add(ingredient)
        db.session.commit()
        
        return jsonify(ingredient.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@ingredients_bp.route('/ingredients/<int:ingredient_id>', methods=['PUT'])
def update_ingredient(ingredient_id):
    """Update an existing ingredient"""
    try:
        ingredient = Ingredient.query.get_or_404(ingredient_id)
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        use_shared_ledger()
        recorded = record_updated_ingredient(
            {
                "name": ledger_ingredient_id(
                    ingredient.name, ingredient.inci_name, ingredient.cas_number
                ),
                "inci_name": ingredient.inci_name,
                "cas_number": ingredient.cas_number,
            },
            data,
        )
        if recorded is not None:
            body, status = recorded
            if status != 200:
                return jsonify({'error': body.get('error', 'ingredient rejected')}), status
        
        # Update fields
        for field in ['name', 'category', 'function', 
                     'description', 'sustainability_score', 'evidence_level']:
            if field in data:
                setattr(ingredient, field, data[field])
        columns = ingredient_column_values(data)
        if 'inci_name' in columns:
            ingredient.inci_name = columns['inci_name']
        if 'cas_number' in columns:
            ingredient.cas_number = columns['cas_number']
        
        # Update price range
        if 'price_range' in data:
            ingredient.price_range_min = data['price_range'].get('min')
            ingredient.price_range_max = data['price_range'].get('max')
        
        # Update regulatory status
        if 'regulatory_status' in data:
            ingredient.set_regulatory_status(data['regulatory_status'])
        
        db.session.commit()
        
        return jsonify(ingredient.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@ingredients_bp.route('/ingredients/<int:ingredient_id>', methods=['DELETE'])
def delete_ingredient(ingredient_id):
    """Delete an ingredient"""
    try:
        ingredient = Ingredient.query.get_or_404(ingredient_id)
        db.session.delete(ingredient)
        db.session.commit()
        
        return jsonify({'message': 'Ingredient deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@ingredients_bp.route('/ingredients/categories', methods=['GET'])
def get_categories():
    """Get all unique ingredient categories"""
    try:
        categories = db.session.query(Ingredient.category).distinct().all()
        category_list = [cat[0] for cat in categories if cat[0]]
        
        return jsonify({'categories': category_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@ingredients_bp.route('/ingredients/search', methods=['POST'])
def search_ingredients():
    """Advanced ingredient search with AI-like capabilities"""
    try:
        data = request.get_json()
        query_text = data.get('query', '')
        filters = data.get('filters', {})
        
        # Start with base query
        query = Ingredient.query
        
        # Apply text search
        if query_text:
            search_term = f"%{query_text}%"
            query = query.filter(
                or_(
                    Ingredient.name.ilike(search_term),
                    Ingredient.inci_name.ilike(search_term),
                    Ingredient.description.ilike(search_term),
                    Ingredient.function.ilike(search_term),
                    Ingredient.category.ilike(search_term)
                )
            )
        
        # Apply filters
        if filters.get('categories'):
            query = query.filter(Ingredient.category.in_(filters['categories']))
            
        if filters.get('evidence_levels'):
            query = query.filter(Ingredient.evidence_level.in_(filters['evidence_levels']))
            
        if filters.get('sustainability_min'):
            query = query.filter(Ingredient.sustainability_score >= filters['sustainability_min'])
            
        if filters.get('price_max'):
            query = query.filter(Ingredient.price_range_max <= filters['price_max'])
        
        # Execute query
        ingredients = query.limit(50).all()
        
        return jsonify({
            'ingredients': [ingredient.to_dict() for ingredient in ingredients],
            'total': len(ingredients)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

