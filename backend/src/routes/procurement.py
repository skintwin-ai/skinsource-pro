from flask import Blueprint, request, jsonify
from src.chain_commands import record_received_lot, use_shared_ledger
from src.models.user import db, User
from src.models import ProcurementRequest, Ingredient, Supplier
from sqlalchemy import or_, and_, desc
from datetime import datetime, date

procurement_bp = Blueprint('procurement', __name__)

@procurement_bp.route('/procurement/requests', methods=['GET'])
def get_procurement_requests():
    """Get all procurement requests with optional filtering"""
    try:
        # Get query parameters
        user_id = request.args.get('user_id', type=int)
        status = request.args.get('status')
        priority = request.args.get('priority')
        ingredient_id = request.args.get('ingredient_id', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # Build query
        query = ProcurementRequest.query
        
        # Apply filters
        if user_id:
            query = query.filter(ProcurementRequest.user_id == user_id)
        
        if status:
            query = query.filter(ProcurementRequest.status == status)
            
        if priority:
            query = query.filter(ProcurementRequest.priority == priority)
            
        if ingredient_id:
            query = query.filter(ProcurementRequest.ingredient_id == ingredient_id)
        
        # Order by created date (newest first)
        query = query.order_by(desc(ProcurementRequest.created_at))
        
        # Paginate results
        pagination = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        requests = []
        for req in pagination.items:
            req_data = req.to_dict()
            # Add ingredient and user info
            if req.ingredient:
                req_data['ingredient'] = req.ingredient.to_dict()
            if req.user:
                req_data['user'] = req.user.to_dict()
            # Add response count
            req_data['response_count'] = len(req.rfq_responses)
            requests.append(req_data)
        
        return jsonify({
            'requests': requests,
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

@procurement_bp.route('/procurement/requests/<int:request_id>', methods=['GET'])
def get_procurement_request(request_id):
    """Get a specific procurement request by ID"""
    try:
        procurement_request = ProcurementRequest.query.get_or_404(request_id)
        
        # Get all responses for this request
        responses = RFQResponse.query.filter_by(
            procurement_request_id=request_id
        ).join(Supplier).all()
        
        response_data = []
        for response in responses:
            resp_data = response.to_dict()
            resp_data['supplier'] = response.supplier.to_dict()
            response_data.append(resp_data)
        
        request_data = procurement_request.to_dict()
        request_data['ingredient'] = procurement_request.ingredient.to_dict()
        request_data['user'] = procurement_request.user.to_dict()
        request_data['responses'] = response_data
        
        return jsonify(request_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/requests', methods=['POST'])
def create_procurement_request():
    """Create a new procurement request"""
    try:
        data = request.get_json()
        
        if not data or 'title' not in data or 'ingredient_id' not in data:
            return jsonify({'error': 'Title and ingredient_id are required'}), 400
        
        # Validate ingredient exists
        ingredient = Ingredient.query.get_or_404(data['ingredient_id'])
        
        # Parse delivery date if provided
        delivery_date = None
        if data.get('delivery_date'):
            try:
                delivery_date = datetime.strptime(data['delivery_date'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid delivery_date format. Use YYYY-MM-DD'}), 400
        
        procurement_request = ProcurementRequest(
            user_id=data.get('user_id', 1),  # Default to user 1 for now
            ingredient_id=data['ingredient_id'],
            title=data['title'],
            description=data.get('description'),
            quantity_needed=data.get('quantity_needed', 0),
            target_price=data.get('target_price'),
            delivery_date=delivery_date,
            priority=data.get('priority', 'medium'),
            status=data.get('status', 'draft')
        )
        
        # Set JSON fields
        if 'specifications' in data:
            import json
            procurement_request.specifications = json.dumps(data['specifications'])
        
        if 'quality_requirements' in data:
            import json
            procurement_request.quality_requirements = json.dumps(data['quality_requirements'])
        
        db.session.add(procurement_request)
        db.session.commit()
        
        return jsonify(procurement_request.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/requests/<int:request_id>', methods=['PUT'])
def update_procurement_request(request_id):
    """Update an existing procurement request"""
    try:
        procurement_request = ProcurementRequest.query.get_or_404(request_id)
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if data.get('status') == 'completed':
            ingredient = Ingredient.query.get(procurement_request.ingredient_id)
            use_shared_ledger()
            if data.get("component_id") and data.get("pieces") is not None:
                from src.chain_commands import record_received_package

                received, received_status = record_received_package(
                    {
                        "component_id": data.get("component_id"),
                        "name": data.get("name") or data.get("component_id"),
                        "lot_id": data.get("lot_id") or f"pack-{procurement_request.id}",
                        "supplier_name": data.get("supplier_name") or "",
                        "pieces": data.get("pieces"),
                    }
                )
                if received_status != 200:
                    return jsonify({"error": received.get("error", "package rejected")}), received_status
            else:
                received, received_status = record_received_lot(
                    {
                        "lot_id": data.get("lot_id") or f"lot-{procurement_request.id}",
                        "ingredient_id": ingredient.name if ingredient is not None else "",
                        "qualification_id": data.get("qualification_id") or "",
                        "milligrams": data.get("milligrams"),
                        "quantity_kg": data.get("quantity_needed", procurement_request.quantity_needed),
                    }
                )
            if received_status != 200:
                return jsonify({"error": received.get("error", "lot rejected")}), received_status

        # Update basic fields
        for field in ['title', 'description', 'quantity_needed', 'target_price', 
                     'priority', 'status']:
            if field in data:
                setattr(procurement_request, field, data[field])
        
        # Update delivery date
        if 'delivery_date' in data and data['delivery_date']:
            try:
                procurement_request.delivery_date = datetime.strptime(
                    data['delivery_date'], '%Y-%m-%d'
                ).date()
            except ValueError:
                return jsonify({'error': 'Invalid delivery_date format. Use YYYY-MM-DD'}), 400
        
        # Update JSON fields
        if 'specifications' in data:
            import json
            procurement_request.specifications = json.dumps(data['specifications'])
        
        if 'quality_requirements' in data:
            import json
            procurement_request.quality_requirements = json.dumps(data['quality_requirements'])
        
        # Update sent_at if status changed to sent
        if data.get('status') == 'sent' and procurement_request.sent_at is None:
            procurement_request.sent_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify(procurement_request.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/requests/<int:request_id>/responses', methods=['POST'])
def create_rfq_response(request_id):
    """Create a new RFQ response"""
    try:
        procurement_request = ProcurementRequest.query.get_or_404(request_id)
        data = request.get_json()
        
        if not data or 'supplier_id' not in data or 'quoted_price' not in data:
            return jsonify({'error': 'Supplier ID and quoted price are required'}), 400
        
        # Validate supplier exists
        supplier = Supplier.query.get_or_404(data['supplier_id'])
        
        # Check if response already exists
        existing = RFQResponse.query.filter_by(
            procurement_request_id=request_id,
            supplier_id=data['supplier_id']
        ).first()
        
        if existing:
            return jsonify({'error': 'Response from this supplier already exists'}), 400
        
        rfq_response = RFQResponse(
            procurement_request_id=request_id,
            supplier_id=data['supplier_id'],
            quoted_price=data['quoted_price'],
            total_price=data.get('total_price'),
            lead_time_days=data.get('lead_time_days', 0),
            minimum_order_quantity=data.get('minimum_order_quantity'),
            terms_conditions=data.get('terms_conditions'),
            payment_terms=data.get('payment_terms'),
            validity_days=data.get('validity_days', 30),
            coa_provided=data.get('coa_provided', False),
            msds_provided=data.get('msds_provided', False),
            notes=data.get('notes')
        )
        
        # Set documents if provided
        if 'documents' in data:
            import json
            rfq_response.documents = json.dumps(data['documents'])
        
        db.session.add(rfq_response)
        
        # Update procurement request status if this is the first response
        if procurement_request.status == 'sent':
            procurement_request.status = 'received'
        
        db.session.commit()
        
        return jsonify(rfq_response.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/responses/<int:response_id>', methods=['PUT'])
def update_rfq_response(response_id):
    """Update an RFQ response"""
    try:
        rfq_response = RFQResponse.query.get_or_404(response_id)
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update fields
        for field in ['quoted_price', 'total_price', 'lead_time_days', 
                     'minimum_order_quantity', 'terms_conditions', 'payment_terms',
                     'validity_days', 'coa_provided', 'msds_provided', 'notes', 'status']:
            if field in data:
                setattr(rfq_response, field, data[field])
        
        # Update documents
        if 'documents' in data:
            import json
            rfq_response.documents = json.dumps(data['documents'])
        
        # Update reviewed_at if status changed to under_review or accepted/rejected
        if data.get('status') in ['under_review', 'accepted', 'rejected']:
            rfq_response.reviewed_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify(rfq_response.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/dashboard', methods=['GET'])
def get_procurement_dashboard():
    """Get procurement dashboard statistics"""
    try:
        # Get counts by status
        total_requests = ProcurementRequest.query.count()
        active_rfqs = ProcurementRequest.query.filter(
            ProcurementRequest.status.in_(['sent', 'received'])
        ).count()
        pending_orders = ProcurementRequest.query.filter_by(status='awarded').count()
        completed_orders = ProcurementRequest.query.filter_by(status='completed').count()
        
        # Get recent activity
        recent_requests = ProcurementRequest.query.order_by(
            desc(ProcurementRequest.created_at)
        ).limit(5).all()
        
        # Calculate savings (mock calculation)
        total_savings = 24500  # This would be calculated from actual data
        
        # Get supplier count
        active_suppliers = Supplier.query.count()
        
        dashboard_data = {
            'statistics': {
                'total_requests': total_requests,
                'active_rfqs': active_rfqs,
                'pending_orders': pending_orders,
                'completed_orders': completed_orders,
                'total_savings': total_savings,
                'active_suppliers': active_suppliers
            },
            'recent_requests': [req.to_dict() for req in recent_requests]
        }
        
        return jsonify(dashboard_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@procurement_bp.route('/procurement/requests/<int:request_id>/send', methods=['POST'])
def send_rfq(request_id):
    """Send RFQ to selected suppliers"""
    try:
        procurement_request = ProcurementRequest.query.get_or_404(request_id)
        data = request.get_json()
        
        supplier_ids = data.get('supplier_ids', [])
        if not supplier_ids:
            return jsonify({'error': 'At least one supplier must be selected'}), 400
        
        # Validate all suppliers exist
        suppliers = Supplier.query.filter(Supplier.id.in_(supplier_ids)).all()
        if len(suppliers) != len(supplier_ids):
            return jsonify({'error': 'One or more suppliers not found'}), 400
        
        # Update request status and sent timestamp
        procurement_request.status = 'sent'
        procurement_request.sent_at = datetime.utcnow()
        
        # Set deadline if provided
        if data.get('deadline'):
            try:
                procurement_request.deadline = datetime.strptime(
                    data['deadline'], '%Y-%m-%d %H:%M:%S'
                )
            except ValueError:
                return jsonify({'error': 'Invalid deadline format. Use YYYY-MM-DD HH:MM:SS'}), 400
        
        db.session.commit()
        
        # In a real application, this would trigger email notifications to suppliers
        
        return jsonify({
            'message': f'RFQ sent to {len(suppliers)} suppliers',
            'request': procurement_request.to_dict(),
            'suppliers': [supplier.to_dict() for supplier in suppliers]
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

