defmodule MyApp.GraphQL.Schema do
  use Absinthe.Schema

  import_types MyApp.GraphQL.UserSchema
  import_types MyApp.GraphQL.OrderSchema, MyApp.GraphQL.CartSchema

  query do
    import_fields :user_queries
    import_fields :order_queries
  end
end

defmodule MyApp.GraphQL.UserSchema do
  use Absinthe.Schema.Notation

  object :user do
    field :id, :id
    field :name, :string
  end
end

defmodule MyApp.GraphQL.OrderSchema do
  use Absinthe.Schema.Notation

  object :order do
    field :id, :id
  end
end

defmodule MyApp.GraphQL.CartSchema do
  use Absinthe.Schema.Notation

  object :cart do
    field :id, :id
  end
end
